"""A sound file attached to a card plays right on the card.

A clip posted with `report.py --attach` (or dropped on the notes) is stored as <hash>-<name>.wav; the card draws an
<audio controls> player for it instead of a download tile, in the conversation, the task's own files and the
all-files tab. The clip is served with byte ranges (server.byte_range), so the player can jump to any point.
Drives the real index.html + i18n.js in headless Chrome over raw CDP, with /api faked by a stub server on a free port
(never the live board, never .task_board).

Run: python3 tests/test_audio_player_ui.py  (stdlib + node 22+ + Google Chrome).
INDEX=<file> serves another index.html (e.g. the previous commit's, to see the test fail on the old page).
SHOT=<file.png> saves a screenshot of the conversation entry."""
import json
import math
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="audio-player-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)
import server  # noqa: E402

NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
INDEX = os.environ.get("INDEX") or os.path.join(ROOT, "index.html")
SHOT = os.environ.get("SHOT", "")
SERVED = {"/": INDEX, "/index.html": INDEX, "/i18n.js": os.path.join(ROOT, "i18n.js")}
OLD = "2026-11-11T09:00:00+00:00"


def wav(seconds=2.0, rate=22050, hz=440):
    n = int(seconds * rate)
    pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / rate))) for i in range(n))
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


CLIP = wav()
LOG_CLIP = "0123456789abcdef-guard_line_01.wav"
TASK_CLIP = "fedcba9876543210-door_creak.wav"
PDF = "00112233445566ff-notes.pdf"
REPORT = {"from": "claude", "status": "progress", "at": OLD, "message": "Here is the first voice line.",
          "images": [LOG_CLIP, PDF]}
COLS = [{"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "done", "name": "Done", "done": True}]
CARD = {"id": "c1", "title": "Card c1", "notes": "Make the guard talk", "priority": "", "tags": [], "column": "prog",
        "parent": None, "order": 0, "created": OLD, "updated": OLD, "images": [TASK_CLIP],
        "agent": {"id": "a-c1", "started": OLD}}
AGENTS = {"c1": {"id": "a-c1", "sessionId": "s-c1", "state": "idle", "phase": "needs_you", "reason": "",
                 "log": [REPORT], "mcp_uses": [], "mcp": None}}
BOARD = {"version": 1, "columns": COLS, "tasks": [CARD]}
RANGES = []


class Stub(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_bytes(self, b, ctype, code=200, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Board-Rev", "1")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
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
        if p.endswith(".wav"):
            # The same range answer the real server gives (server.byte_range).
            RANGES.append(self.headers.get("Range"))
            part = server.byte_range(self.headers.get("Range"), len(CLIP))
            if part and part != "bad":
                a, b = part
                return self.send_bytes(CLIP[a:b + 1], "audio/wav", 206, {"Accept-Ranges": "bytes",
                                                                       "Content-Range": f"bytes {a}-{b}/{len(CLIP)}"})
            return self.send_bytes(CLIP, "audio/wav", headers={"Accept-Ranges": "bytes"})
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
  '--no-first-run', '--no-default-browser-check', '--autoplay-policy=no-user-gesture-required', '--mute-audio',
  'about:blank'], { stdio: 'ignore' });
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
await ev(`openDetail('c1')`); await ev(`showTab('conversation')`); await sleep(600);
const names = (sel) => `[...document.querySelectorAll('${sel}')].map((a) => a.getAttribute('src').split('/').pop())`;
res.logPlayers = await ev(names('#d-log .thumbs audio'));
res.logTiles = await ev(`[...document.querySelectorAll('#d-log .thumbs .thumb')].map((e) => e.tagName + '.' + [...e.classList].join('.'))`);
res.taskPlayers = await ev(names('#d-images audio'));
// Metadata loads (duration known), it plays, and a jump to 1.5 s sticks (needs byte ranges).
res.media = await ev(`await (async () => { const a = document.querySelector('#d-log audio');
  for (let i = 0; i < 50 && !(a.readyState >= 1); i++) await new Promise((r) => setTimeout(r, 100));
  const duration = Math.round(a.duration * 10) / 10;
  let played = 'ok'; try { await a.play(); } catch (e) { played = String(e); }
  await new Promise((r) => setTimeout(r, 300)); const moved = a.currentTime > 0; a.pause();
  a.currentTime = 1.5; await new Promise((r) => setTimeout(r, 300));
  return { duration, played, moved, seekTo: Math.round(a.currentTime * 10) / 10, error: a.error && a.error.code }; })()`);
res.width = await ev(`Math.round(document.querySelector('#d-log .thumb.audio').getBoundingClientRect().width)`);
if (shot) {
  const r = await ev(`(() => { const b = document.querySelector('#d-log .thumb.audio').closest('li').getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; })()`);
  const s = await send('Page.captureScreenshot', { format: 'png', clip: { x: Math.max(0, r.x - 8), y: Math.max(0, r.y - 8), width: r.w + 16, height: r.h + 16, scale: 1 } });
  writeFileSync(shot, Buffer.from(s.result.data, 'base64'));
}
await ev(`showTab('images')`); await sleep(300);
res.gridPlayers = await ev(names('#d-allimgs audio'));
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
    check("the report's clip gets a player", res["logPlayers"], [LOG_CLIP])
    check("the clip is a player tile, the PDF stays a link tile", res["logTiles"], ["DIV.thumb.file.audio", "A.thumb.file"])
    check("a clip on the task itself gets a player", res["taskPlayers"], [TASK_CLIP])
    check("the all-files tab shows both players", sorted(res["gridPlayers"]), sorted([LOG_CLIP, TASK_CLIP]))
    m = res["media"]
    if "exc" in m:
        print("media check threw:", m["exc"])
    check("the clip loads (2 s long)", (m.get("duration"), m.get("error")), (2.0, None))
    check("it plays", (m.get("played"), m.get("moved")), ("ok", True))
    check("a jump to 1.5 s sticks", m.get("seekTo"), 1.5)
    check("the player is wide enough to use", isinstance(res["width"], int) and res["width"] >= 280, True)
    check("the page asked for byte ranges", any(r for r in RANGES), True)
    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
