#!/usr/bin/env python3
"""Serveur de test Minitel : page web + WebSocket écho sur un seul port.

- Aucune dépendance : uniquement la bibliothèque standard de Python 3.8+.
- Écoute en HTTP simple sur le port 25715 (le HTTPS est géré par ecloudserv).
- GET /        -> index.html (l'émulateur Minitel)
- GET /ws      -> WebSocket (service Minitel, voir service.py)
- GET /health  -> "ok"

Lancer :  python3 server.py
Changer le port :  PORT=8080 python3 server.py
"""
import asyncio
import base64
import hashlib
import os
import struct

from service import Session

PORT = int(os.environ.get("PORT", "25715"))
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 1 << 20  # 1 Mo
BASE = os.path.dirname(os.path.abspath(__file__))

def http_response(status, body, content_type="text/html; charset=utf-8"):
    if isinstance(body, str):
        body = body.encode("utf-8")
    head = (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    )
    return head.encode("latin-1") + body


async def read_request(reader):
    data = await reader.readuntil(b"\r\n\r\n")
    lines = data.decode("latin-1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return method, path.split("?", 1)[0], headers


def make_frame(opcode, payload=b""):
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


async def read_frame(reader):
    b1, b2 = await reader.readexactly(2)
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", await reader.readexactly(8))[0]
    if n > MAX_FRAME:
        raise ValueError("trame trop grande")
    mask = await reader.readexactly(4) if masked else None
    data = await reader.readexactly(n)
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return fin, opcode, data


async def websocket_session(reader, writer, headers):
    key = headers.get("sec-websocket-key")
    if not key:
        writer.write(http_response("400 Bad Request", "Clé WebSocket manquante"))
        return
    accept = base64.b64encode(
        hashlib.sha1(key.encode() + GUID).digest()
    ).decode()
    writer.write(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode("latin-1")
    )
    await writer.drain()

    session = Session()
    writer.write(make_frame(2, session.start()))
    await writer.drain()

    buffer, buffer_op = b"", None
    while True:
        fin, op, data = await read_frame(reader)
        if op == 8:  # close
            writer.write(make_frame(8, data[:2]))
            await writer.drain()
            return
        if op == 9:  # ping -> pong
            writer.write(make_frame(10, data))
            await writer.drain()
            continue
        if op == 10:  # pong
            continue
        if op in (1, 2):
            buffer_op, buffer = op, data
        elif op == 0:
            buffer += data
        if fin and buffer_op is not None:
            reponse = session.on_input(buffer)
            if reponse:
                writer.write(make_frame(2, reponse))
                await writer.drain()
            buffer, buffer_op = b"", None


async def handle(reader, writer):
    try:
        method, path, headers = await read_request(reader)
        is_ws = headers.get("upgrade", "").lower() == "websocket"
        if path == "/ws" and is_ws:
            await websocket_session(reader, writer, headers)
        elif path == "/health":
            writer.write(http_response("200 OK", "ok", "text/plain; charset=utf-8"))
        elif path == "/" and method == "GET":
            try:
                with open(os.path.join(BASE, "index.html"), "rb") as f:
                    writer.write(http_response("200 OK", f.read()))
            except OSError:
                writer.write(http_response("404 Not Found", "index.html manquant"))
        else:
            writer.write(http_response("404 Not Found", "Introuvable"))
        await writer.drain()
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError,
            ConnectionError, ValueError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    print(f"Serveur à l'écoute sur le port {PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
