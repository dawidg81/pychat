# server.py
import socket
import threading

PACKET_CLIENT_ID = 0x00
PACKET_MESSAGE   = 0x01

MAX_NAME   = 16
MAX_MOTD   = 64
MAX_MSG    = 64
MAX_USER   = 16

print("pychat server v0")

while True:
    serverName = input("Server Name: ").strip()
    if len(serverName) == 0 or len(serverName) > MAX_NAME:
        print(f"Server name must be 1–{MAX_NAME} characters.")
        continue

    serverMOTD = input("Server MOTD: ").strip()
    if len(serverMOTD) > MAX_MOTD:
        print(f"MOTD can not be longer than {MAX_MOTD} characters.")
        continue
    break

try:
    PORT = int(input("Port: "))
except ValueError:
    print("Port must be an integer.")
    quit()

clients: dict[str, socket.socket] = {}  # username -> conn
clients_lock = threading.Lock()


def recv_exact(conn: socket.socket, n: int) -> bytes | None:
    """Read exactly n bytes from conn, return None on disconnect."""
    buf = b''
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def send_server_id(conn: socket.socket):
    name_bytes = serverName.encode('ascii').ljust(MAX_NAME)[:MAX_NAME]
    motd_bytes  = serverMOTD.encode('ascii').ljust(MAX_MOTD)[:MAX_MOTD]
    packet = bytes([PACKET_CLIENT_ID]) + name_bytes + motd_bytes
    conn.sendall(packet)


def send_message(conn: socket.socket, text: str):
    msg_bytes = text.encode('ascii').ljust(MAX_MSG)[:MAX_MSG]
    packet = bytes([PACKET_MESSAGE]) + msg_bytes
    conn.sendall(packet)


def broadcast(text: str, exclude: str | None = None):
    """Send a message packet to all connected clients, optionally skipping one."""
    msg_bytes = text.encode('ascii').ljust(MAX_MSG)[:MAX_MSG]
    packet = bytes([PACKET_MESSAGE]) + msg_bytes
    with clients_lock:
        for username, conn in list(clients.items()):
            if username == exclude:
                continue
            try:
                conn.sendall(packet)
            except OSError:
                pass  # will be cleaned up by that client's thread


def handle_client(conn: socket.socket, addr):
    username = None
    try:
        # --- handshake: receive client id packet ---
        packet_id = recv_exact(conn, 1)
        if packet_id is None or packet_id[0] != PACKET_CLIENT_ID:
            print(f"[{addr}] Bad handshake packet id. Closing.")
            conn.close()
            return

        name_bytes = recv_exact(conn, MAX_USER)
        if name_bytes is None:
            print(f"[{addr}] Disconnected during handshake.")
            conn.close()
            return

        username = name_bytes.decode('ascii').strip()
        if not username:
            print(f"[{addr}] Empty username. Closing.")
            conn.close()
            return

        with clients_lock:
            if username in clients:
                print(f"[{addr}] Username '{username}' already taken. Closing.")
                conn.close()
                return
            clients[username] = conn

        print(f"[+] {username} connected from {addr}")
        send_server_id(conn)
        broadcast(f"*** {username} has joined the chat ***")

        # --- message loop ---
        while True:
            packet_id = recv_exact(conn, 1)
            if packet_id is None:
                break
            if packet_id[0] != PACKET_MESSAGE:
                print(f"[{username}] Unknown packet id 0x{packet_id[0]:02x}, ignoring.")
                recv_exact(conn, MAX_MSG)  # drain unknown packet body
                continue

            msg_bytes = recv_exact(conn, MAX_MSG)
            if msg_bytes is None:
                break

            message = msg_bytes.decode('ascii').strip()
            print(f"[{username}]: {message}")
            broadcast(f"{username}: {message}")  # echo to everyone incl. sender

    except (OSError, UnicodeDecodeError) as e:
        print(f"[{addr}] Error: {e}")
    finally:
        if username:
            with clients_lock:
                clients.pop(username, None)
            print(f"[-] {username} disconnected.")
            broadcast(f"*** {username} has left the chat ***")
        conn.close()


with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('', PORT))
    s.listen()
    print(f"Listening on port {PORT}...")

    while True:
        conn, addr = s.accept()
        t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
        t.start()
