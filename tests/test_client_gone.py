"""A page that goes away before its answer is sent (closed tab, reload, a slow update check) is not an error: the
server must not print a traceback for it, and must keep answering other requests.

Run: python3 tests/test_client_gone.py  (stdlib only; a fresh temp HOME)."""
import http.server
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="client-gone-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

errors = []


class Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):  # what prints the "Exception occurred" block
        errors.append(sys.exc_info()[1])


started = threading.Event()


def slow_status(force):
    started.set()
    time.sleep(0.5)  # the page is gone by now
    return {"version": "1", "behind": 0}


server.UPDATER.status = slow_status
srv = Server(("127.0.0.1", 0), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]

for case in range(3):
    started.clear()
    c = socket.create_connection(("127.0.0.1", port))
    c.sendall(f"GET /api/update?force=1 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
    assert started.wait(5), "the request never reached the update check"
    c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # close with a reset, like a killed tab
    c.close()
    time.sleep(1)
assert not errors, f"a closed page was reported as a server error: {errors!r}"
print("ok  a page closed during the update check: no traceback")

with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/update") as r:
    assert r.status == 200 and b'"behind": 0' in r.read()
print("ok  the server still answers afterwards")
srv.shutdown()
print("PASS")
