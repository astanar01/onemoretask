"""A many-part question's screenshots show under the part they belong to, not at the end of the message.

report.py stores an `--image` given after a `--part` on that part (parts[k].images); one given before the first
--part stays on the message (images). The card draws each part's thumbnails between its question and its choices,
and the message's own images after the send row as before. The all-images grid counts part images too.
Drives the real index.html + i18n.js in headless Chrome over raw CDP, with /api faked by a stub server on a free port
(never the live board, never .task_board).

Run: python3 tests/test_question_part_images_ui.py  (stdlib + node 22+ + Google Chrome).
INDEX=<file> serves another index.html (e.g. the previous commit's, to see the test fail on the old page).
SHOT=<file.png> saves a screenshot of the question."""
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import zlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="question-part-images-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
INDEX = os.environ.get("INDEX") or os.path.join(ROOT, "index.html")
SHOT = os.environ.get("SHOT", "")
SERVED = {"/": INDEX, "/index.html": INDEX, "/i18n.js": os.path.join(ROOT, "i18n.js")}
OLD = "2026-11-11T09:00:00+00:00"


def png(rgb, w=160, h=100):
    raw = b"".join(b"\0" + bytes(rgb) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PICS = {"red.png": png((200, 60, 60)), "green.png": png((60, 170, 80)), "blue.png": png((60, 90, 210)),
        "gold.png": png((210, 170, 40))}
QUESTION = {"from": "claude", "status": "question", "at": OLD, "message": "Two things to settle before I resize:",
            "images": ["gold.png"],
            "parts": [{"q": "1. Are the tables the right size?", "options": ["a) Yes", "b) No"], "images": ["red.png", "green.png"]},
                      {"q": "2. And the chairs?", "options": ["a) Yes", "b) No"]},
                      {"q": "3. And the barrels?", "options": ["a) Yes", "b) No"], "images": ["blue.png"]}]}
COLS = [{"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "done", "name": "Done", "done": True}]
CARD = {"id": "c1", "title": "Card c1", "notes": "", "priority": "", "tags": [], "column": "prog", "parent": None,
        "order": 0, "created": OLD, "updated": OLD, "agent": {"id": "a-c1", "started": OLD}}
AGENTS = {"c1": {"id": "a-c1", "sessionId": "s-c1", "state": "idle", "phase": "needs_you", "reason": "",
                 "log": [QUESTION], "mcp_uses": [], "mcp": None}}
BOARD = {"version": 1, "columns": COLS, "tasks": [CARD]}


class Stub(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_bytes(self, b, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Board-Rev", "1")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/projects":
            return self.send_bytes(json.dumps([{"id": "demo", "name": "demo", "path": TMP,
                                                "board": os.path.join(TMP, ".task_board")}]).encode(), "application/json")
        if p == "/api/tasks":
            return self.send_bytes(json.dumps(BOARD).encode(), "application/json")
        if p == "/api/agents":
            return self.send_bytes(json.dumps(AGENTS).encode(), "application/json")
        if p == "/api/app-copy":
            return self.send_bytes(json.dumps({"current": ROOT, "copies": [{"path": ROOT, "main": True}]}).encode(),
                                   "application/json")
        if p.startswith("/api/"):
            return self.send_bytes(b"{}", "application/json")
        if "/agent_reports/images/" in p and p.rsplit("/", 1)[1] in PICS:
            return self.send_bytes(PICS[p.rsplit("/", 1)[1]], "image/png")
        if p in SERVED:
            return self.send_bytes(open(SERVED[p], "rb").read(),
                                   "text/javascript" if p == "/i18n.js" else "text/html; charset=utf-8")
        self.send_bytes(b"", "text/plain", 404)

    def do_PUT(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        return self.send_bytes(b'{"ok": true}', "application/json")

    do_POST = do_PUT


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


DRIVER = r"""
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
const [url, chrome, dport, profile, shot] = process.argv.slice(2);
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
await send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 1000, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url });
let ready = null;
for (let i = 0; i < 100 && ready !== true; i++) {
  await sleep(100);
  ready = await ev(`typeof polledOnce !== 'undefined' && polledOnce && state.tasks.length > 0 && Object.keys(agents).length > 0`);
}
const res = { ready };
await ev(`openDetail('c1')`); await ev(`showTab('conversation')`); await sleep(400);
// Per part: [question, thumbnail names, does the first thumbnail sit between the question and the choices].
res.parts = await ev(`[...document.querySelectorAll('#d-log .qpart')].map((p) => { const th = [...p.querySelectorAll('.thumbs img')];
  const q = p.querySelector('.qpq').getBoundingClientRect(), o = p.querySelector('.qopts').getBoundingClientRect(), t = th[0] && th[0].getBoundingClientRect();
  return [p.querySelector('.qpq').textContent, th.map((i) => i.getAttribute('src').split('/').pop()), t ? t.top >= q.bottom - 1 && t.bottom <= o.top + 1 : null]; })`);
// The message's own thumbnails: those of the log entry outside every part.
res.msgImgs = await ev(`[...document.querySelectorAll('#d-log li.k-question .thumbs img')].filter((i) => !i.closest('.qpart')).map((i) => i.getAttribute('src').split('/').pop())`);
res.loaded = await ev(`[...document.querySelectorAll('#d-log .qpart .thumbs img')].every((i) => i.complete && i.naturalWidth === 160)`);
res.allCount = await ev(`$('#d-imgcount').textContent`);
if (shot) {
  const r = await ev(`(() => { const b = $('#d-log li.k-question').getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; })()`);
  const s = await send('Page.captureScreenshot', { format: 'png', clip: { x: Math.max(0, r.x - 8), y: Math.max(0, r.y - 8), width: r.w + 16, height: r.h + 16, scale: 1 } });
  writeFileSync(shot, Buffer.from(s.result.data, 'base64'));
}
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
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome"), SHOT],
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
    print("page:", INDEX)
    check("page loaded and polled", res["ready"])
    check("no page errors", res["errors"], [])
    check("each part shows its own pictures between its question and its choices", res["parts"],
          [["1. Are the tables the right size?", ["red.png", "green.png"], True],
           ["2. And the chairs?", [], None],
           ["3. And the barrels?", ["blue.png"], True]])
    check("part pictures load", res["loaded"])
    check("the message's own picture stays on the message, once", res["msgImgs"], ["gold.png"])
    check("the all-images count includes part pictures", res["allCount"], "4")
    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
