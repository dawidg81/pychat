# client.py
import socket
import threading

PACKET_CLIENT_ID = 0x00
PACKET_MESSAGE   = 0x01

MAX_NAME = 16
MAX_MOTD = 64
MAX_MSG  = 64
MAX_USER = 16

print("pychat client v0")

HOST = input("Address: ").strip()
try:
    PORT = int(input("Port: "))
except ValueError:
    print("Port must be an integer.")
    quit()

username = input("Username: ").strip()
if not username or len(username) > MAX_USER:
    print(f"Username must be 1–{MAX_USER} characters.")
    quit()

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((HOST, PORT))


def recv_exact(n: int) -> bytes | None:
    buf = b''
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def send_client_id():
    name_bytes = username.encode('ascii').ljust(MAX_USER)[:MAX_USER]
    packet = bytes([PACKET_CLIENT_ID]) + name_bytes
    s.sendall(packet)


def send_message(text: str):
    msg_bytes = text.encode('ascii').ljust(MAX_MSG)[:MAX_MSG]
    packet = bytes([PACKET_MESSAGE]) + msg_bytes
    s.sendall(packet)


def receive_loop():
    """Runs in a background thread — prints incoming packets."""
    while True:
        packet_id = recv_exact(1)
        if packet_id is None:
            print("\n[disconnected from server]")
            break

        if packet_id[0] == PACKET_CLIENT_ID:
            # server id packet
            name_bytes = recv_exact(MAX_NAME)
            motd_bytes  = recv_exact(MAX_MOTD)
            if name_bytes is None or motd_bytes is None:
                print("\n[disconnected during handshake]")
                break
            server_name = name_bytes.decode('ascii').strip()
            server_motd = motd_bytes.decode('ascii').strip()
            print(f"Connected to '{server_name}' — {server_motd}")

        elif packet_id[0] == PACKET_MESSAGE:
            msg_bytes = recv_exact(MAX_MSG)
            if msg_bytes is None:
                print("\n[disconnected from server]")
                break
            message = msg_bytes.decode('ascii').strip()
            print(f"\r{message}\n> ", end='', flush=True)

        else:
            print(f"[unknown packet id 0x{packet_id[0]:02x}]")


# --- connect and handshake ---
send_client_id()

recv_thread = threading.Thread(target=receive_loop, daemon=True)
recv_thread.start()

# --- input loop ---
while True:
    try:
        text = input("> ").strip()
        if not text:
            continue
        send_message(text)
    except (EOFError, KeyboardInterrupt):
        print("\n[disconnecting]")
        s.close()
        break
