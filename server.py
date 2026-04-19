"""
pychat server v1
Packets:
  0x00  handshake   client->server: [0x00][16B username]
                    server->client: [0x00][16B server name][64B MOTD]
  0x01  message     both ways:      [0x01][64B message]
  0x02  kick        server->client: [0x02][64B reason]
"""

import socket
import threading
import sqlite3
import os
import time
import datetime

# ── constants ────────────────────────────────────────────────────────────────
PACKET_HANDSHAKE = 0x00
PACKET_MESSAGE   = 0x01
PACKET_KICK      = 0x02

SZ_USER  = 16
SZ_NAME  = 16
SZ_MOTD  = 64
SZ_MSG   = 64
SZ_KICK  = 64

DEFAULT_ROOM = "general"
OPS_FILE       = "ops.txt"
BLACKLIST_FILE = "blacklist.txt"
DB_FILE        = "db.sqlite3"

# ── server config (filled at startup) ────────────────────────────────────────
server_name: str = ""
server_motd: str = ""
server_port: int = 0

# ── state ────────────────────────────────────────────────────────────────────
clients_lock = threading.Lock()
# username -> {"conn": socket, "room": str, "addr": tuple, "user_id": int}
clients: dict[str, dict] = {}

rooms_lock = threading.Lock()
# room_name -> set of usernames
rooms: dict[str, set] = {}


# ══════════════════════════════════════════════════════════════════════════════
# Database
# ══════════════════════════════════════════════════════════════════════════════

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

db_conn = db_connect()
db_lock = threading.Lock()


def db_init():
    with db_lock:
        db_conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    UNIQUE NOT NULL,
                first_seen    INTEGER NOT NULL,
                last_seen     INTEGER NOT NULL,
                time_spent    INTEGER NOT NULL DEFAULT 0,
                times_kicked  INTEGER NOT NULL DEFAULT 0,
                ever_banned   INTEGER NOT NULL DEFAULT 0,
                is_online     INTEGER NOT NULL DEFAULT 0,
                current_room  TEXT    NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rooms (
                name TEXT PRIMARY KEY
            );
        """)
        # ensure default room exists
        db_conn.execute(
            "INSERT OR IGNORE INTO rooms (name) VALUES (?)", (DEFAULT_ROOM,)
        )
        # load persisted rooms into memory
        for row in db_conn.execute("SELECT name FROM rooms"):
            rooms[row["name"]] = set()
        db_conn.commit()


def db_get_or_create_user(username: str) -> sqlite3.Row:
    now = int(time.time())
    with db_lock:
        row = db_conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            db_conn.execute(
                "INSERT INTO users (username, first_seen, last_seen) VALUES (?, ?, ?)",
                (username, now, now),
            )
            db_conn.commit()
            row = db_conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
    return row


def db_set_online(username: str, online: bool, room: str = ""):
    with db_lock:
        now = int(time.time())
        db_conn.execute(
            "UPDATE users SET is_online=?, current_room=?, last_seen=? WHERE username=?",
            (1 if online else 0, room if online else "", now, username),
        )
        db_conn.commit()


def db_add_time(username: str, seconds: int):
    with db_lock:
        db_conn.execute(
            "UPDATE users SET time_spent = time_spent + ? WHERE username=?",
            (seconds, username),
        )
        db_conn.commit()


def db_increment_kicks(username: str):
    with db_lock:
        db_conn.execute(
            "UPDATE users SET times_kicked = times_kicked + 1 WHERE username=?",
            (username,),
        )
        db_conn.commit()


def db_set_ever_banned(username: str):
    with db_lock:
        db_conn.execute(
            "UPDATE users SET ever_banned = 1 WHERE username=?", (username,)
        )
        db_conn.commit()


def db_get_user(username: str) -> sqlite3.Row | None:
    with db_lock:
        return db_conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()


def db_add_room(name: str):
    with db_lock:
        db_conn.execute("INSERT OR IGNORE INTO rooms (name) VALUES (?)", (name,))
        db_conn.commit()


def db_del_room(name: str):
    with db_lock:
        db_conn.execute("DELETE FROM rooms WHERE name=?", (name,))
        db_conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# ops / blacklist helpers
# ══════════════════════════════════════════════════════════════════════════════

def _read_file_set(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {line.strip().lower() for line in f if line.strip()}


def _write_file_set(path: str, names: set[str]):
    with open(path, "w") as f:
        for n in sorted(names):
            f.write(n + "\n")


def is_op(username: str) -> bool:
    return username.lower() in _read_file_set(OPS_FILE)


def add_op(username: str):
    s = _read_file_set(OPS_FILE)
    s.add(username.lower())
    _write_file_set(OPS_FILE, s)


def remove_op(username: str):
    s = _read_file_set(OPS_FILE)
    s.discard(username.lower())
    _write_file_set(OPS_FILE, s)


def is_banned(username: str) -> bool:
    return username.lower() in _read_file_set(BLACKLIST_FILE)


def add_ban(username: str):
    s = _read_file_set(BLACKLIST_FILE)
    s.add(username.lower())
    _write_file_set(BLACKLIST_FILE, s)


def remove_ban(username: str):
    s = _read_file_set(BLACKLIST_FILE)
    s.discard(username.lower())
    _write_file_set(BLACKLIST_FILE, s)


# ══════════════════════════════════════════════════════════════════════════════
# Packet helpers
# ══════════════════════════════════════════════════════════════════════════════

def recv_exact(conn: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def send_server_id(conn: socket.socket):
    name_b = server_name.encode("ascii").ljust(SZ_NAME)[:SZ_NAME]
    motd_b = server_motd.encode("ascii").ljust(SZ_MOTD)[:SZ_MOTD]
    conn.sendall(bytes([PACKET_HANDSHAKE]) + name_b + motd_b)


def _send_msg_packet(conn: socket.socket, text: str):
    msg_b = text.encode("ascii", errors="replace").ljust(SZ_MSG)[:SZ_MSG]
    conn.sendall(bytes([PACKET_MESSAGE]) + msg_b)


def _send_kick_packet(conn: socket.socket, reason: str):
    r_b = reason.encode("ascii", errors="replace").ljust(SZ_KICK)[:SZ_KICK]
    conn.sendall(bytes([PACKET_KICK]) + r_b)


# ══════════════════════════════════════════════════════════════════════════════
# Messaging / room helpers
# ══════════════════════════════════════════════════════════════════════════════

def send_to(username: str, text: str):
    with clients_lock:
        client = clients.get(username)
    if client:
        try:
            _send_msg_packet(client["conn"], text)
        except OSError:
            pass


def broadcast_room(room: str, text: str, exclude: str | None = None):
    with rooms_lock:
        members = set(rooms.get(room, set()))
    for username in members:
        if username == exclude:
            continue
        send_to(username, text)


def broadcast_all(text: str, exclude: str | None = None):
    with clients_lock:
        targets = list(clients.keys())
    for username in targets:
        if username == exclude:
            continue
        send_to(username, text)


def get_room(username: str) -> str:
    with clients_lock:
        return clients.get(username, {}).get("room", DEFAULT_ROOM)


def move_to_room(username: str, new_room: str):
    old_room = get_room(username)
    with rooms_lock:
        if old_room in rooms:
            rooms[old_room].discard(username)
        rooms.setdefault(new_room, set()).add(username)
    with clients_lock:
        if username in clients:
            clients[username]["room"] = new_room
    db_set_online(username, True, new_room)
    if old_room != new_room:
        broadcast_room(old_room, f"*** {username} left #{old_room} ***")
        broadcast_room(new_room, f"*** {username} joined #{new_room} ***", exclude=username)
        send_to(username, f"[server] You are now in #{new_room}")


# ══════════════════════════════════════════════════════════════════════════════
# Command handling
# ══════════════════════════════════════════════════════════════════════════════

COMMANDS = {
    "/help":     "Show this help",
    "/cmdlist":  "Alias for /help",
    "/userlist": "List online users in your room",
    "/rooms":    "List all rooms",
    "/newroom":  "/newroom <name>  — create a room  [OP]",
    "/delroom":  "/delroom <name>  — delete a room  [OP]",
    "/join":     "/join <room>     — join a room",
    "/kick":     "/kick <user> [reason]  [OP]",
    "/ban":      "/ban <user> [reason]   [OP]",
    "/unban":    "/unban <user>          [OP]",
    "/op":       "/op <user>             [OP]",
    "/deop":     "/deop <user>           [OP]",
    "/info":     "/info <user>   — show user profile",
    "/pm":       "/pm <user> <message>   — private message",
    "/me":       "/me <action>",
}


def fmt_time(seconds: int) -> str:
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"


def fmt_ts(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def handle_command(sender: str, raw: str):
    parts = raw.strip().split()
    cmd   = parts[0].lower()
    args  = parts[1:]
    op    = is_op(sender)

    def reply(text: str):
        send_to(sender, f"[server] {text}")

    # ── /help / /cmdlist ──────────────────────────────────────────────────────
    if cmd in ("/help", "/cmdlist"):
        send_to(sender, "[server] Available commands:")
        for name, desc in COMMANDS.items():
            send_to(sender, f"  {name:<12} {desc}")

    # ── /userlist ─────────────────────────────────────────────────────────────
    elif cmd == "/userlist":
        room = get_room(sender)
        with rooms_lock:
            members = list(rooms.get(room, set()))
        op_tag = lambda u: " [OP]" if is_op(u) else ""
        reply(f"Users in #{room}: " + ", ".join(u + op_tag(u) for u in sorted(members)))

    # ── /rooms ────────────────────────────────────────────────────────────────
    elif cmd == "/rooms":
        with rooms_lock:
            room_list = {r: len(m) for r, m in rooms.items()}
        lines = [f"  #{r} ({n} online)" for r, n in sorted(room_list.items())]
        reply("Rooms:\n" + "\n".join(lines))

    # ── /join ─────────────────────────────────────────────────────────────────
    elif cmd == "/join":
        if not args:
            reply("Usage: /join <room>")
            return
        target = args[0].lstrip("#")
        with rooms_lock:
            if target not in rooms:
                reply(f"Room #{target} does not exist. Use /rooms to list rooms.")
                return
        move_to_room(sender, target)

    # ── /newroom [OP] ─────────────────────────────────────────────────────────
    elif cmd == "/newroom":
        if not op:
            reply("You are not an operator.")
            return
        if not args:
            reply("Usage: /newroom <name>")
            return
        rname = args[0].lstrip("#")
        with rooms_lock:
            if rname in rooms:
                reply(f"Room #{rname} already exists.")
                return
            rooms[rname] = set()
        db_add_room(rname)
        broadcast_all(f"[server] Room #{rname} created by {sender}")

    # ── /delroom [OP] ─────────────────────────────────────────────────────────
    elif cmd == "/delroom":
        if not op:
            reply("You are not an operator.")
            return
        if not args:
            reply("Usage: /delroom <name>")
            return
        rname = args[0].lstrip("#")
        if rname == DEFAULT_ROOM:
            reply(f"Cannot delete #{DEFAULT_ROOM}.")
            return
        with rooms_lock:
            if rname not in rooms:
                reply(f"Room #{rname} does not exist.")
                return
            members = set(rooms[rname])
        for u in members:
            send_to(u, f"[server] Room #{rname} was deleted. Moving you to #{DEFAULT_ROOM}.")
            move_to_room(u, DEFAULT_ROOM)
        with rooms_lock:
            rooms.pop(rname, None)
        db_del_room(rname)
        broadcast_all(f"[server] Room #{rname} deleted by {sender}")

    # ── /kick [OP] ────────────────────────────────────────────────────────────
    elif cmd == "/kick":
        if not op:
            reply("You are not an operator.")
            return
        if not args:
            reply("Usage: /kick <user> [reason]")
            return
        target  = args[0]
        reason  = " ".join(args[1:]) if len(args) > 1 else "Kicked by operator"
        kick_user(target, reason, kicked_by=sender)

    # ── /ban [OP] ─────────────────────────────────────────────────────────────
    elif cmd == "/ban":
        if not op:
            reply("You are not an operator.")
            return
        if not args:
            reply("Usage: /ban <user> [reason]")
            return
        target = args[0]
        reason = " ".join(args[1:]) if len(args) > 1 else "Banned by operator"
        add_ban(target)
        db_set_ever_banned(target)
        kick_user(target, f"Banned: {reason}", kicked_by=sender)
        reply(f"{target} has been banned.")

    # ── /unban [OP] ───────────────────────────────────────────────────────────
    elif cmd == "/unban":
        if not op:
            reply("You are not an operator.")
            return
        if not args:
            reply("Usage: /unban <user>")
            return
        target = args[0]
        remove_ban(target)
        reply(f"{target} has been unbanned.")

    # ── /op [OP] ──────────────────────────────────────────────────────────────
    elif cmd == "/op":
        if not op:
            reply("You are not an operator.")
            return
        if not args:
            reply("Usage: /op <user>")
            return
        add_op(args[0])
        reply(f"{args[0]} is now an operator.")
        send_to(args[0], "[server] You have been granted operator status.")

    # ── /deop [OP] ────────────────────────────────────────────────────────────
    elif cmd == "/deop":
        if not op:
            reply("You are not an operator.")
            return
        if not args:
            reply("Usage: /deop <user>")
            return
        remove_op(args[0])
        reply(f"{args[0]} is no longer an operator.")
        send_to(args[0], "[server] Your operator status has been removed.")

    # ── /info ─────────────────────────────────────────────────────────────────
    elif cmd == "/info":
        if not args:
            reply("Usage: /info <user>")
            return
        row = db_get_user(args[0])
        if row is None:
            reply(f"User '{args[0]}' not found.")
            return
        room_str = f"#{row['current_room']}" if row["is_online"] else "offline"
        reply(
            f"── Info: {row['username']} ──\n"
            f"  ID         : {row['id']}\n"
            f"  First seen : {fmt_ts(row['first_seen'])}\n"
            f"  Last seen  : {fmt_ts(row['last_seen'])}\n"
            f"  Time spent : {fmt_time(row['time_spent'])}\n"
            f"  Kicks      : {row['times_kicked']}\n"
            f"  Ever banned: {'Yes' if row['ever_banned'] else 'No'}\n"
            f"  Status     : {room_str}\n"
            f"  Operator   : {'Yes' if is_op(row['username']) else 'No'}"
        )

    # ── /pm ───────────────────────────────────────────────────────────────────
    elif cmd == "/pm":
        if len(args) < 2:
            reply("Usage: /pm <user> <message>")
            return
        target  = args[0]
        message = " ".join(args[1:])
        send_to(target, f"[PM from {sender}] {message}")
        send_to(sender, f"[PM to {target}] {message}")

    # ── /me ───────────────────────────────────────────────────────────────────
    elif cmd == "/me":
        if not args:
            reply("Usage: /me <action>")
            return
        action = " ".join(args)
        room   = get_room(sender)
        broadcast_room(room, f"* {sender} {action}")

    else:
        reply(f"Unknown command '{cmd}'. Type /help for a list.")


# ══════════════════════════════════════════════════════════════════════════════
# Kick helper
# ══════════════════════════════════════════════════════════════════════════════

def kick_user(username: str, reason: str, kicked_by: str = "server"):
    with clients_lock:
        client = clients.get(username)
    if client is None:
        send_to(kicked_by, f"[server] '{username}' is not online.")
        return
    try:
        _send_kick_packet(client["conn"], reason)
    except OSError:
        pass
    room = client["room"]
    broadcast_room(room, f"[server] {username} was kicked ({reason})")
    db_increment_kicks(username)
    # close will trigger handle_client cleanup via recv returning None
    try:
        client["conn"].shutdown(socket.SHUT_RDWR)
        client["conn"].close()
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Client thread
# ══════════════════════════════════════════════════════════════════════════════

def handle_client(conn: socket.socket, addr):
    username   = None
    join_time  = None
    try:
        # ── handshake ────────────────────────────────────────────────────────
        pkt = recv_exact(conn, 1)
        if pkt is None or pkt[0] != PACKET_HANDSHAKE:
            conn.close()
            return

        name_bytes = recv_exact(conn, SZ_USER)
        if name_bytes is None:
            conn.close()
            return

        username = name_bytes.decode("ascii", errors="replace").strip()
        if not username:
            conn.close()
            return

        if is_banned(username):
            _send_kick_packet(conn, "You are banned from this server.")
            conn.close()
            return

        with clients_lock:
            if username in clients:
                _send_kick_packet(conn, "Username already in use.")
                conn.close()
                return
            clients[username] = {"conn": conn, "room": DEFAULT_ROOM, "addr": addr}

        with rooms_lock:
            rooms.setdefault(DEFAULT_ROOM, set()).add(username)

        row = db_get_or_create_user(username)
        db_set_online(username, True, DEFAULT_ROOM)
        join_time = int(time.time())

        print(f"[+] {username} connected from {addr}")
        send_server_id(conn)
        send_to(username, f"[server] Welcome, {username}! You are in #{DEFAULT_ROOM}. Type /help for commands.")
        broadcast_room(DEFAULT_ROOM, f"*** {username} joined #{DEFAULT_ROOM} ***", exclude=username)

        # ── message loop ─────────────────────────────────────────────────────
        while True:
            pkt = recv_exact(conn, 1)
            if pkt is None:
                break
            pid = pkt[0]

            if pid == PACKET_MESSAGE:
                msg_bytes = recv_exact(conn, SZ_MSG)
                if msg_bytes is None:
                    break
                message = msg_bytes.decode("ascii", errors="replace").strip()
                if not message:
                    continue

                if message.startswith("/"):
                    handle_command(username, message)
                else:
                    room = get_room(username)
                    print(f"[{room}] {username}: {message}")
                    broadcast_room(room, f"{username}: {message}")

            else:
                # drain unknown packet (fixed body assumed SZ_MSG)
                recv_exact(conn, SZ_MSG)

    except (OSError, UnicodeDecodeError) as e:
        print(f"[{addr}] Error: {e}")
    finally:
        if username:
            room = get_room(username)
            with clients_lock:
                clients.pop(username, None)
            with rooms_lock:
                if room in rooms:
                    rooms[room].discard(username)
            if join_time:
                db_add_time(username, int(time.time()) - join_time)
            db_set_online(username, False)
            broadcast_room(room, f"*** {username} left #{room} ***")
            print(f"[-] {username} disconnected.")
        try:
            conn.close()
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    global server_name, server_motd, server_port

    print("pychat server v1")
    db_init()

    while True:
        server_name = input("Server Name: ").strip()
        if not server_name or len(server_name) > SZ_NAME:
            print(f"Server name must be 1–{SZ_NAME} characters.")
            continue
        server_motd = input("Server MOTD: ").strip()
        if len(server_motd) > SZ_MOTD:
            print(f"MOTD must be ≤{SZ_MOTD} characters.")
            continue
        break

    try:
        server_port = int(input("Port: "))
    except ValueError:
        print("Port must be an integer.")
        return

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", server_port))
        s.listen()
        print(f"Listening on port {server_port}...")

        while True:
            conn, addr = s.accept()
            threading.Thread(
                target=handle_client, args=(conn, addr), daemon=True
            ).start()


if __name__ == "__main__":
    main()
