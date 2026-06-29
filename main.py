#!/usr/bin/env python3

import socket
import threading
import select
import os

PORT = int(os.environ.get("PORT", "8080"))

def forward(src, dst):
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except:
        pass
    finally:
        try:
            src.close()
        except:
            pass
        try:
            dst.close()
        except:
            pass

def handle(client):
    try:
        # greeting
        client.recv(2)
        nmethods = client.recv(1)[0]
        client.recv(nmethods)

        # no auth
        client.sendall(b"\x05\x00")

        # request
        ver, cmd, _, atyp = client.recv(4)

        if atyp == 1:  # IPv4
            addr = socket.inet_ntoa(client.recv(4))
        elif atyp == 3:  # domain
            length = client.recv(1)[0]
            addr = client.recv(length).decode()
        else:
            client.close()
            return

        port = int.from_bytes(client.recv(2), "big")

        remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote.connect((addr, port))

        client.sendall(
            b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        )

        threading.Thread(
            target=forward,
            args=(client, remote),
            daemon=True
        ).start()

        forward(remote, client)

    except Exception:
        try:
            client.close()
        except:
            pass

server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("0.0.0.0", PORT))
server.listen(100)

print(f"SOCKS5 listening on :{PORT}")

while True:
    client, _ = server.accept()
    threading.Thread(
        target=handle,
        args=(client,),
        daemon=True
    ).start()
