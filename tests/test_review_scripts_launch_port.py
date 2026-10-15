"""launch.py treats the port as "board running" only when the task board answers there, not any program.

    python3 tests/test_review_scripts_launch_port.py
"""
import http.server
import os
import socket
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import launch  # noqa: E402

fails = []


def check(name, ok):
    print(("ok  " if ok else "FAIL") + " " + name)
    if not ok:
        fails.append(name)


def serve(projects_body):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/projects" and projects_body is not None:
                body, code = projects_body, 200
            else:
                body, code = (b"<html>other app</html>", 200) if self.path == "/" else (b"nope", 404)
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, "http://127.0.0.1:%d" % s.server_address[1]


opened = []
launch.webbrowser.open = opened.append
launch.start_muninn = lambda: None

other, url = serve(None)
html, url2 = serve(b"<html>also not the board</html>")
board, url3 = serve(b'[{"id": "p1"}]')
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    free = "http://127.0.0.1:%d" % s.getsockname()[1]
try:
    check("a program with no /api/projects is not the board", launch.answers(url) not in (True, "board"))
    check("a program serving HTML at /api/projects is not the board", launch.answers(url2) not in (True, "board"))
    check("the board's project list counts as the board", launch.answers(url3) in (True, "board"))
    check("nothing on the port reads as free", not launch.answers(free))
    port = url.rsplit(":", 1)[1]
    try:
        launch.main(["--port", port])
        check("main on a port taken by another program stops", False)
    except SystemExit as e:
        check("main on a port taken by another program stops with a message", bool(e.code) and opened == [])
    launch.main(["--port", url3.rsplit(":", 1)[1]])
    check("main on a running board opens it", opened == [url3])
finally:
    for s in (other, html, board):
        s.shutdown()

sys.exit(1 if fails else 0)
