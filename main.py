import asyncio, socket

print "simplechat v0.0.0 2025-12-21 16:17"

PORT = 5000

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind(('', PORT))
    s.listen()
    conn, addr = s.accept()

    with conn:
        print(f"New connection incoming from {addr}")

        while True:
            username = username.decode('utf-8').strip()

            if not clientId:
                print "New connection did not send anything. Closing it!"
                break
            
            serverId = {"The Classical Grand", "Welcome!"}
