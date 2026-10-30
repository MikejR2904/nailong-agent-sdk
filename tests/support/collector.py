import gzip
import http.server
import json
import threading


class Collector:
    def __init__(self):
        self.requests = []
        self.script = []
        self.default = {"status": 200, "body": {}}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                outer.requests.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "json": json.loads(raw),
                    }
                )
                item = outer.script.pop(0) if outer.script else outer.default
                body = item.get("body", {})
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(item.get("status", 200))
                for name, value in item.get("headers", {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
