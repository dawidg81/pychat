import socket

print("pychat server v0 2025-12-22 17:23")

while True:

    serverName = input("Name of the server? ")
    
    if len(serverName) > 16:
        print("Name of the server can not be longer than 16 characters!")

    serverMOTD = input("Message Of The Day? ")

    if len(serverMOTD) > 64:
        print("Message Of The Day can not be longer than 64 characters!")

PORT = input("On what port number this server will listen? ")

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
            
            serverId = {serverName, serverMOTD}
