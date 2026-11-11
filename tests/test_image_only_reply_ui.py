"""A message sent with only images (no text) must not hide the reply box until a reload.

The server logs an images-only message as "See the attached images.", but the page kept its pending copy with the
empty text, so the copy never matched the logged entry, stayed pending for good, and replyOpen() hid the composer.
Drives the real index.html + i18n.js in headless Chrome over raw CDP, with /api faked by a stub server on a free port
that logs a reply the way server.py does (never the live board, never .task_board).

Run: python3 tests/test_image_only_reply_ui.py  (stdlib + node 22+ + Google Chrome).
INDEX=<file> serves another index.html (e.g. the previous commit's, to see the test fail on the old page);
OLD_SERVER=1 leaves "message" out of the reply answer, as the server did before the fix."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="image-only-reply-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
INDEX = os.environ.get("INDEX") or os.path.join(ROOT, "index.html")
OLD_SERVER = bool(os.environ.get("OLD_SERVER"))
SERVED = {"/": INDEX, "/index.html": INDEX, "/i18n.js": os.path.join(ROOT, "i18n.js")}
OLD = "2026-11-11T09:00:00+00:00"

COLS = [{"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "done", "name": "Done", "done": True}]
TASKS = [{"id": "c1", "title": "Card c1", "notes": "", "priority": "", "tags": [], "column": "prog", "parent": None,
          "order": 0, "created": OLD, "updated": OLD, "agent": {"id": "a1", "started": OLD}}]
AGENTS = {"c1": {"id": "a1", "sessionId": "s1", "state": "working", "phase": "working", "reason": "",
                 "log": [{"from": "claude", "status": "progress", "at": OLD, "message": "Working on it"}],
                 "mcp_uses": [], "mcp": None}}
BOARD = {"version": 1, "columns": COLS, "tasks": TASKS}
LOCK = threading.Lock()


class Stub(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Board-Rev", "1")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/projects":
            return self.send_json([{"id": "demo", "name": "demo", "path": TMP, "board": os.path.join(TMP, ".task_board")}])
        if p == "/api/tasks":
            return self.send_json(BOARD)
        if p == "/api/agents":
            with LOCK:
                return self.send_json(AGENTS)
        if p == "/api/app-copy":
            return self.send_json({"current": ROOT, "copies": [{"path": ROOT, "main": True}]})
        if p.startswith("/api/"):
            return self.send_json({})
        if p in SERVED:
            b = open(SERVED[p], "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8" if p != "/i18n.js" else "text/javascript")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        self.send_response(404)
        self.end_headers()

    def do_PUT(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        return self.send_json({"ok": True})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.split("?")[0] != "/api/agent/reply":
            return self.send_json({"ok": True})
        # The same text rule as server.py's /api/agent/reply; a working session logs it as a note.
        images = body.get("images") or []
        text = body["text"].strip() or ("See the attached images." if images else "Go ahead.")
        entry = {"from": "you", "status": "note", "at": datetime.now(timezone.utc).isoformat(), "message": text}
        if images:
            entry["images"] = images
        with LOCK:
            AGENTS["c1"]["log"] = AGENTS["c1"]["log"] + [entry]
        return self.send_json({"ok": True, "queued": True} if OLD_SERVER else {"ok": True, "queued": True, "message": text})


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


DRIVER = r"""
import { spawn } from 'node:child_process';
const [url, chrome, dport, profile] = process.argv.slice(2);
const proc = spawn(chrome, ['--headless=new', `--remote-debugging-port=${dport}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', 'about:blank'], { stdio: 'ignore' });
const quit = (code) => { try { proc.kill(); } catch {} setTimeout(() => process.exit(code), 300); };
setTimeout(() => { console.log(JSON.stringify({ error: 'HUNG driver' })); quit(2); }, 60000);
let targets;
for (let i = 0; i < 75 && !targets; i++) {
  try { targets = await (await fetch(`http://127.0.0.1:${dport}/json`)).json(); } catch { await new Promise(r => setTimeout(r, 200)); }
}
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let id = 0; const pending = new Map(); const errors = [];
ws.addEventListener('message', ({ data }) => {
  const m = JSON.parse(data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  else if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
});
const send = (method, params = {}) => new Promise(r => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, replMode: true }); return r.result.exceptionDetails ? { exc: r.result.exceptionDetails.exception?.description } : r.result.result.value; };
const sleep = (ms) => new Promise(res => setTimeout(res, ms));
await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1300, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url });
let ready = null;
for (let i = 0; i < 100 && ready !== true; i++) {
  await sleep(100);
  ready = await ev(`typeof polledOnce !== 'undefined' && polledOnce && state.tasks.length > 0 && Object.keys(agents).length > 0`);
}
const res = { ready };
const look = `({ box: $('#d-reply-wrap').classList.contains('on'), pending: (pending.c1 || []).length,
  last: [...document.querySelectorAll('#d-log > li .msg')].map((m) => m.textContent).pop() })`;
await ev(`openDetail('c1'); showTab('conversation')`); await sleep(100);
res.before = await ev(look);
// Images only: an empty text box and one pasted image.
await ev(`$('#d-reply').value = ''; replyImages = ['pic.png']; renderReplyImages()`);
await ev(`await sendReply()`);
for (let i = 0; i < 3; i++) { await ev(`await pollAgents()`); await sleep(150); }
res.imagesOnly = await ev(look);
// Then a normal text note still works and clears.
await ev(`$('#d-reply').value = 'more words'`);
await ev(`await sendReply()`);
for (let i = 0; i < 3; i++) { await ev(`await pollAgents()`); await sleep(150); }
res.text = await ev(look);
res.errors = errors;
console.log(JSON.stringify(res));
quit(0);
"""

fails = passes = 0


def check(name, got, want=True):
    global fails, passes
    ok = got == want
    fails += not ok
    passes += ok
    print(("ok  " if ok else "FAIL") + f" {name}" + ("" if ok else f": got {got!r}, want {want!r}"))


def main():
    port, dport = free_port(), free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    driver = os.path.join(TMP, "driver.mjs")
    open(driver, "w").write(DRIVER)
    try:
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome")],
                           capture_output=True, text=True, timeout=90)
    finally:
        srv.shutdown()
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        print("FAIL driver gave no result\nstdout:", p.stdout[-2000:], "\nstderr:", p.stderr[-2000:])
        return 1
    if "error" in res:
        print("FAIL", res["error"])
        return 1
    print("page:", INDEX, "| server answer:", "old (no message)" if OLD_SERVER else "new (with message)")
    check("page loaded and polled", res["ready"])
    check("no page errors", res["errors"], [])
    check("reply box shows on a working card", res["before"]["box"])
    check("images only: the message shows with the logged text", res["imagesOnly"]["last"], "See the attached images.")
    check("images only: nothing left pending", res["imagesOnly"]["pending"], 0)
    check("images only: the reply box is still there", res["imagesOnly"]["box"])
    check("text note: shows, clears, box stays", (res["text"]["last"], res["text"]["pending"], res["text"]["box"]),
          ("more words", 0, True))
    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
