import http.server
import threading

import pytest

from tests.support.openai_provider import Fake


@pytest.fixture
def server():
    Fake.state = {"requests": [], "script": []}
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", Fake.state
    httpd.shutdown()
    httpd.server_close()
