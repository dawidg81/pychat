import socket
import ipaddress

print("pychat client v0 2025-12-22 17:24")

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
HOST = str(input("Address: "))
PORT = int(input("Port: "))

s.connect((HOST, PORT))

def handleServer(str):
    username = input("Username: ")
    s.send(username)

    s.send('e'.encode())
    serverId = ''
    serverId = s.recv(80).decode('ascii').strip()
    print("You have connected to ", serverId)

while True:
    handleServer(s)
    message = input("message: ")

s.close()
