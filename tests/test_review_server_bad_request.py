"""Bad request bodies answer 400 with a clear message, never 404, 500 or a hung request.

A missing key (body["taskId"]) raised KeyError, a LookupError, so it answered 404 "'taskId'". A negative
Content-Length made rfile.read(-1) wait for the client to close; a non-numeric one crashed the PUT handler; a JSON
list body answered 500.

Run: python3 tests/test_review_server_bad_request.py  (stdlib only; a fresh temp HOME, a free port)."""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="bad-request-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def request(method, path, body=b"", length=None):
    """Raw request so Content-Length can be anything; returns (status, json body) or ("timeout", None)."""
    head = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body) if length is None else length}\r\nConnection: close\r\n\r\n")
    s = socket.create_connection(("127.0.0.1", port))
    s.settimeout(3)
    try:
        s.sendall(head.encode() + body)
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        return "timeout", None
    finally:
        s.close()
    if not data:
        return "no answer", None
    status = int(data.split(b" ", 2)[1])
    payload = data.split(b"\r\n\r\n", 1)[1]
    return status, json.loads(payload) if payload else None


fails = []


def check(name, got, want_status, want_text=None):
    status, body = got
    ok = status == want_status and (want_text is None or want_text in (body or {}).get("error", ""))
    print(("ok  " if ok else "FAIL") + f" {name}: {status} {body}")
    if not ok:
        fails.append(name)


try:
    proj = os.path.join(TMP, "proj")
    os.makedirs(proj)
    pid = server.open_project(proj).id

    check("missing taskId -> 400", request("POST", "/api/agent", json.dumps({"p": pid}).encode()), 400,
          "missing field: taskId")
    check("missing p -> 400", request("POST", "/api/agent/reply", b"{}"), 400, "missing field: p")
    check("unknown project still 404", request("POST", "/api/agent", json.dumps({"p": "nope", "taskId": "t"}).encode()),
          404, "unknown project")
    check("JSON list body -> 400", request("POST", "/api/agent", b"[1, 2]"), 400)
    check("negative Content-Length POST -> 400", request("POST", "/api/agent", b"{}", length=-1), 400)
    check("non-numeric Content-Length POST -> 400", request("POST", "/api/agent", b"{}", length="abc"), 400)
    check("negative Content-Length PUT -> 400", request("PUT", f"/api/tasks?p={pid}", b"{}", length=-1), 400)
    check("non-numeric Content-Length PUT -> 400", request("PUT", f"/api/tasks?p={pid}", b"{}", length="abc"), 400)
    good = json.dumps({"columns": [], "tasks": []}).encode()
    check("valid PUT still saves", request("PUT", f"/api/tasks?p={pid}", good), 204)
    check("JSON list PUT -> 400", request("PUT", f"/api/tasks?p={pid}", b"[]"), 400)
finally:
    httpd.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)

if fails:
    raise SystemExit(f"FAIL {len(fails)}: {fails}")
print("PASS")
