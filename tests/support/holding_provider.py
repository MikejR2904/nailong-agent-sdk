import json
import socket
import threading

from tests.support.chat_provider import final_chat


class HoldingProvider:
    def __init__(self, *, stream=False):
        self.stream = stream
        self.release = threading.Event()
        self.received = threading.Event()
        self.client_closed = threading.Event()
        self.responded = threading.Event()
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self):
        self.release.set()
        self._server.close()

    def _serve(self):
        while True:
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection):
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = connection.recv(65536)
                if not chunk:
                    return
                data += chunk
            head, _, body = data.partition(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":")[1])
            while len(body) < length:
                body += connection.recv(65536)
            if self.stream:
                connection.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    b"Connection: close\r\n\r\n"
                    + b'data: {"id": "chatcmpl-stream-7", "choices": [{"index": 0, '
                    b'"delta": {"content": "par"}}]}\n\n'
                )
            self.received.set()
            connection.settimeout(0.05)
            while not self.release.is_set():
                try:
                    if connection.recv(1) == b"":
                        self.client_closed.set()
                        return
                except TimeoutError:
                    continue
                except OSError:
                    self.client_closed.set()
                    return
            payload = json.dumps(final_chat()).encode("utf-8")
            try:
                connection.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                    + payload
                )
                self.responded.set()
            except OSError:
                self.client_closed.set()
        finally:
            connection.close()
