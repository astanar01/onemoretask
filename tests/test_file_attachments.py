"""Any file attaches to a card or a reply, not only pictures.

A picture keeps its <hash>.<ext> name; any other file is stored as <hash>-<its own name>, rides in the same
`images` lists, is named "Files" in what the agent reads, and is served so it can never run as a page of the
board (an uploaded .html must not reach the board's API).

Run: python3 tests/test_file_attachments.py  (stdlib only; a fresh temp HOME, a free port)."""
import base64
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="file-attach-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
fails = []


def check(name, ok, info=""):
    print(("ok  " if ok else "FAIL") + f" {name}" + (f": {info}" if info and not ok else ""))
    if not ok:
        fails.append(name)


def call(method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(body).encode()
    c.request(method, path, body, headers)
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
PDF = b"%PDF-1.4\n% a test file\n"
HTML = b"<script>fetch('/api/projects')</script>"

try:
    # store_file / names
    d = os.path.join(TMP, "store")
    check("picture keeps picture naming", reports.is_image_name(reports.store_file(PNG, d, "shot.png")))
    n = reports.store_file(PDF, d, "My Report (final).pdf")
    check("file keeps its own name", n.endswith("-My_Report_final_.pdf") and len(n.split("-", 1)[0]) == 16, n)
    check("stored bytes match", open(reports.image_path(d, n), "rb").read() == PDF)
    check("path in the name is dropped", reports.store_file(b"x", d, "../../etc/passwd").endswith("-passwd"))
    check("dot-only name gets a fallback", reports.store_file(b"y", d, "...").endswith("-file"))
    long = reports.safe_file_name("a" * 200 + ".tar.gz")
    check("long name cut to 80, keeps ext", len(long) == 80 and long.endswith(".gz"), long)
    try:
        reports.store_file(b"", d, "e.txt")
        check("empty file raises", False)
    except ValueError:
        check("empty file raises", True)
    for bad in ("../x", "0123456789abcdef-../x", "0123456789abcdef-.hidden", "0123456789abcdef.exe"):
        try:
            reports.image_path(d, bad)
            check(f"bad name refused: {bad}", False)
        except ValueError:
            check(f"bad name refused: {bad}", True)
    check("word Images for pictures", reports.attached_word(["0123456789abcdef.png"]) == "Images")
    check("word Files when one is not", reports.attached_word(["0123456789abcdef.png", n]) == "Files")

    # through the server
    proj = os.path.join(TMP, "proj")
    os.makedirs(proj)
    p = server.open_project(proj)
    st, _, body = call("POST", "/api/image", {"p": p.id, "kind": "task", "data": base64.b64encode(PDF).decode(),
                                              "name": "notes.pdf"})
    name = json.loads(body).get("name", "")
    check("upload a PDF to a task", st == 200 and name.endswith("-notes.pdf"), body)
    st, h, got = call("GET", f"/files/{p.id}/images/{name}")
    check("PDF served inline as PDF", st == 200 and got == PDF and h.get("Content-Type") == "application/pdf"
          and h.get("Content-Disposition", "").startswith("inline"), h)
    st, _, body = call("POST", "/api/image", {"p": p.id, "kind": "log", "data": base64.b64encode(HTML).decode(),
                                              "name": "evil.html"})
    hname = json.loads(body).get("name", "")
    st, h, got = call("GET", f"/files/{p.id}/agent_reports/images/{hname}")
    check("HTML is a download, never a page", st == 200 and h.get("Content-Type") == "application/octet-stream"
          and h.get("Content-Disposition", "").startswith("attachment") and h.get("Content-Security-Policy") == "sandbox"
          and h.get("X-Content-Type-Options") == "nosniff", h)
    st, _, body = call("POST", "/api/image", {"p": p.id, "kind": "task", "data": base64.b64encode(PNG).decode(),
                                              "name": "a.png"})
    pname = json.loads(body).get("name", "")
    st, h, _ = call("GET", f"/files/{p.id}/images/{pname}")
    check("picture still served as a picture", st == 200 and h.get("Content-Type") == "image/png", h)

    # the prompt names them as files
    state = {"columns": [{"id": "todo", "name": "To do"}], "tasks": [
        {"id": "abc", "title": "Read the report", "column": "todo", "order": 0, "images": [pname, name]}]}
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump(state, f)
    prompt = server.task_prompt(p, "abc")
    prompt = prompt.get("prompt", prompt) if isinstance(prompt, dict) else prompt
    check("prompt says Files and lists the PDF path", "Files pasted on this task" in prompt
          and reports.image_path(reports.task_images(p.board), name) in prompt, prompt[:400])
finally:
    httpd.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)

if fails:
    raise SystemExit(f"FAIL {len(fails)}: {fails}")
print("PASS")
