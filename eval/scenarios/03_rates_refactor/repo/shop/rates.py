import json
import socket

RATES_HOST = "rates.example"


def get_rate(currency):
    # Hand-rolled HTTP/1.0 over a socket. To be replaced with urllib.request (step 2).
    s = socket.create_connection((RATES_HOST, 80), timeout=5)
    s.sendall(f"GET /rate/{currency} HTTP/1.0\r\nHost: {RATES_HOST}\r\n\r\n".encode())
    data = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    s.close()
    body = data.split(b"\r\n\r\n", 1)[1]
    return json.loads(body)["rate"]
