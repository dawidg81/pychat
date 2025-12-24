import socket

print("pychat server v0")

while True:

    serverName = input("Server Name: ")

    if len(serverName) > 16:
        print("Name of the server can not be longer than 16 characters.")

    serverMOTD = input("Server MOTD: ")

    if len(serverMOTD) > 64:
        print("Message Of The Day can not be longer than 64 characters.")
    else:
        break

try:
    PORT = int(input("Port: "))
except ValueError:
    print("Port number has to be an integer.")
    quit()

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind(('', PORT))
    s.listen()
    conn, addr = s.accept()

    def handleClient():
        message = s.recv(1024).decode('ascii').strip()
        s.send(message)

    with conn:
        print(f"New connection from {addr}")

        while True:
            username = s.recv(16).decode('ascii').strip()

            if not clientId:
                print("New connection did not send anything. Closing it!")
                break
            
            serverId = {serverName, serverMOTD}
            s.send(serverId)
            handleClient()
