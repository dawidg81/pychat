"""
pychat client v1
Packets:
  0x00  handshake   client->server: [0x00][16B username]
                    server->client: [0x00][16B server name][64B MOTD]
  0x01  message     both ways:      [0x01][64B message]
  0x02  kick        server->client: [0x02][64B reason]
"""

import socket
import threading
import sys
import os

# ── constants ────────────────────────────────────────────────────────────────
PACKET_HANDSHAKE = 0x00
PACKET_MESSAGE   = 0x01
PACKET_KICK      = 0x02

SZ_USER  = 16
SZ_NAME  = 16
SZ_MOTD  = 64
SZ_MSG   = 64
SZ_KICK  = 64

# ── state ─────────────────────────────────────────────────────────────────────
sock: socket.socket | None = None
current_room  = "general"
username_g    = ""
input_lock    = threading.Lock()
running       = threading.Event()
running.set()

# ── terminal helpers ──────────────────────────────────────────────────────────

PROMPT = "> "

def clear_line():
    """Erase the current input line so a server print doesn't corrupt it."""
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()


def print_msg(text: str):
    """
    Print a server/chat message above the prompt without corrupting user input.
    Works on any ANSI terminal (Windows 10+, Linux, macOS).
    """
    with input_lock:
        clear_line()
        print(text)
        sys.stdout.write(PROMPT)
        sys.stdout.flush()


def update_prompt(new_room: str | None = None):
    global current_room
    if new_room:
        current_room = new_room
    with input_lock:
        sys.stdout.write(f"\r\033[K{PROMPT}")
        sys.stdout.flush()


# ── packet helpers ────────────────────────────────────────────────────────────

def recv_exact(n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def send_handshake():
    name_b = username_g.encode("ascii").ljust(SZ_USER)[:SZ_USER]
    sock.sendall(bytes([PACKET_HANDSHAKE]) + name_b)


def send_message(text: str):
    msg_b = text.encode("ascii", errors="replace").ljust(SZ_MSG)[:SZ_MSG]
    sock.sendall(bytes([PACKET_MESSAGE]) + msg_b)


# ── receive thread ────────────────────────────────────────────────────────────

def parse_room_from_msg(text: str) -> str | None:
    """
    Server sends messages like 'You are now in #roomname' when we change rooms.
    Parse that to update the local prompt.
    """
    marker = "You are now in #"
    idx    = text.find(marker)
    if idx != -1:
        return text[idx + len(marker):].strip()
    return None


def receive_loop():
    global current_room

    while running.is_set():
        pkt = recv_exact(1)
        if pkt is None:
            if running.is_set():
                print_msg("\n[disconnected from server]")
                running.clear()
            break

        pid = pkt[0]

        if pid == PACKET_HANDSHAKE:
            name_b = recv_exact(SZ_NAME)
            motd_b = recv_exact(SZ_MOTD)
            if name_b is None or motd_b is None:
                print_msg("[error] Disconnected during handshake.")
                running.clear()
                break
            sname = name_b.decode("ascii", errors="replace").strip()
            smotd = motd_b.decode("ascii", errors="replace").strip()
            print_msg(f"╔══ Connected to '{sname}' ══╗")
            print_msg(f"║  {smotd}")
            print_msg(f"╚{'═' * (len(smotd) + 2)}╝")

        elif pid == PACKET_MESSAGE:
            msg_b = recv_exact(SZ_MSG)
            if msg_b is None:
                print_msg("[error] Disconnected mid-message.")
                running.clear()
                break
            message = msg_b.decode("ascii", errors="replace").strip()

            # detect room change so we can update the prompt
            room = parse_room_from_msg(message)
            if room:
                current_room = room

            print_msg(message)

        elif pid == PACKET_KICK:
            reason_b = recv_exact(SZ_KICK)
            reason   = reason_b.decode("ascii", errors="replace").strip() if reason_b else "No reason given"
            print_msg(f"\n╔══ KICKED ══╗")
            print_msg(f"║  {reason}")
            print_msg(f"╚{'═' * (len(reason) + 2)}╝")
            running.clear()
            break

        else:
            # unknown packet — drain SZ_MSG bytes and ignore
            recv_exact(SZ_MSG)


# ── input loop ────────────────────────────────────────────────────────────────

def input_loop():
    while running.is_set():
        try:
            sys.stdout.write(PROMPT)
            sys.stdout.flush()
            line = input()
        except (EOFError, KeyboardInterrupt):
            break

        line = line.strip()
        if not line:
            continue

        if line.lower() in ("/quit", "/exit", "/q"):
            print("Disconnecting...")
            break

        try:
            send_message(line)
        except OSError:
            print("[error] Lost connection to server.")
            break

    running.clear()


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    global sock, username_g

    print("pychat client v1")
    print("─" * 30)

    HOST = input("Address : ").strip()
    try:
        PORT = int(input("Port    : "))
    except ValueError:
        print("Port must be an integer.")
        return

    username_g = input("Username: ").strip()
    if not username_g or len(username_g) > SZ_USER:
        print(f"Username must be 1–{SZ_USER} characters.")
        return

    print(f"Connecting to {HOST}:{PORT} ...")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((HOST, PORT))
    except OSError as e:
        print(f"Connection failed: {e}")
        return

    send_handshake()

    recv_thread = threading.Thread(target=receive_loop, daemon=True)
    recv_thread.start()

    input_loop()

    running.clear()
    try:
        sock.shutdown(socket.SHUT_RDWR)
        sock.close()
    except OSError:
        pass

    recv_thread.join(timeout=2)
    print("Goodbye.")


if __name__ == "__main__":
    main()
