"""Board columns: a card moved into another column by code (sent to Claude, Status picker) lands on top of it, and
a column keeps scrolling through the 4-second agent polls: a scrollbar drag and wheel scrolling are not cut short
by the redraw (the redraw waits until the button is up and the wheel is still). Drives the real index.html +
i18n.js in headless Chrome over raw CDP, with /api faked by a stub server on a free port (never the live board,
never .task_board). Each run uses a fresh temp dir and Chrome profile.

Run: python3 tests/test_list_resize.py  (stdlib + node 22+ + Google Chrome)."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="column-scroll-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}

W, H = 1400, 700
COLS = [{"id": "todo", "name": "To Do", "done": False},
        {"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "done", "name": "Done", "done": True}]
T0 = "2026-10-15T10:00:00+00:00"
N = 12
TASKS = [{"id": f"p{i}", "title": f"Working card {i} " + "words " * 12, "notes": "", "priority": "", "tags": ["a"], "column": "prog",
          "parent": None, "order": i, "created": T0, "updated": T0, "agent": {"session": f"s{i}"}} for i in range(N)]
TASKS += [{"id": "new", "title": "New card", "notes": "", "priority": "", "tags": [], "column": "todo", "parent": None,
           "order": 0, "created": T0, "updated": T0},
          {"id": "d0", "title": "Old done", "notes": "", "priority": "", "tags": [], "column": "done", "parent": None,
           "order": 0, "created": T0, "updated": T0}]
BOARD = {"version": 1, "columns": COLS, "tasks": TASKS}
polls = [0]


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
            # Every poll differs, like a working session's log and context: each poll redraws the board.
            polls[0] += 1
            return self.send_json({f"p{i}": {"phase": "working", "ctx": polls[0], "log": []} for i in range(N)})
        if p == "/api/app-copy":
            return self.send_json({"current": ROOT, "copies": [{"path": ROOT, "main": True}]})
        if p.startswith("/api/"):
            return self.send_json({})
        if p in SERVED:
            b = open(os.path.join(ROOT, SERVED[p]), "rb").read()
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

    do_POST = do_PUT


def free_port():
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        if port != 8765:   # the live board
            return port


DRIVER = r"""
import { spawn } from 'node:child_process';
const [url, chrome, dport, profile, W, H] = process.argv.slice(2);
const proc = spawn(chrome, ['--headless=new', `--remote-debugging-port=${dport}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', 'about:blank'], { stdio: 'ignore' });
const quit = (code) => { try { proc.kill(); } catch {} setTimeout(() => process.exit(code), 300); };
setTimeout(() => { console.log(JSON.stringify({ error: 'HUNG driver' })); quit(2); }, 90000);
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
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
await send('Page.enable'); await send('Runtime.enable');
const metrics = (w) => send('Emulation.setDeviceMetricsOverride', { width: w, height: +H, deviceScaleFactor: 1, mobile: false });
await metrics(+W);

let initId = null;
const setView = async (view) => {
  if (initId) await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: initId });
  const r = await send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('task-board:view', '${view}'); localStorage.setItem('task-board:theme', 'light'); } catch (e) {}` });
  initId = r.result.identifier;
};
const load = async (view, nav) => {
  await setView(view);
  if (nav) await send('Page.navigate', { url }); else await send('Page.reload', { ignoreCache: true });
  let r = null;
  for (let i = 0; i < 100; i++) {
    await sleep(100);
    r = await ev(`typeof polledOnce !== 'undefined' && polledOnce && document.querySelectorAll('.lcard, .card').length > 0`);
    if (r === true) break;
  }
  await sleep(300);
  return r === true;
};
const res = {};
res.ready = await load('board', true);
// Count redraws of the board view.
await ev(`window.__r = 0; const _r = render; render = function () { window.__r++; return _r.apply(this, arguments); }; true`);
const col = `document.querySelector('[data-col="prog"] .cards')`;
const top = (c) => ev(`(() => { const ids = [...document.querySelectorAll('[data-col="${c}"] .card')].map(x => x.dataset.id); return ids; })()`);

// 1. A scrollbar drag that spans several polls keeps following the mouse.
const g = await ev(`(() => { const c = ${col}, r = c.getBoundingClientRect(); return { x: r.right - 4, top: r.top, sbw: c.offsetWidth - c.clientWidth, max: c.scrollHeight - c.clientHeight }; })()`);
res.geom = g;
const btn = (type, y) => send('Input.dispatchMouseEvent', { type, x: g.x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1 });
let y = g.top + 20;
await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: g.x, y, button: 'none', buttons: 0 });
await btn('mousePressed', y);
const r0 = await ev('window.__r');
const drag = [];
for (let i = 0; i < 45; i++) { y += 4; await btn('mouseMoved', y); await sleep(200); drag.push(await ev(`${col}.scrollTop`)); }
res.dragRendersDuring = (await ev('window.__r')) - r0;
await btn('mouseReleased', y);
res.drag = drag;
await sleep(1200);
res.dragRendersAfter = (await ev('window.__r')) - r0;
res.afterDrag = await ev(`${col}.scrollTop`);

// 2. Wheel scrolling back up across polls is not cut short either.
await ev(`${col}.scrollTop = ${col}.scrollHeight; true`);
const c = await ev(`(() => { const r = ${col}.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
const r1 = await ev('window.__r');
const wheel = [];
for (let i = 0; i < 40; i++) {
  await send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: c[0], y: c[1], deltaX: 0, deltaY: -25 });
  await sleep(150);
  wheel.push(await ev(`${col}.scrollTop`));
}
res.wheel = wheel;
res.wheelRendersDuring = (await ev('window.__r')) - r1;
await sleep(1500);
res.wheelRendersAfter = (await ev('window.__r')) - r1;

// 3. A card moved by code into another column goes on top of it; same-column null still means the end.
res.toProg = await ev(`moveTask('new', 'prog', null); render(); [...document.querySelectorAll('[data-col="prog"] .card')].map(x => x.dataset.id)`);
res.toDone = await ev(`moveTask('p5', 'done', null); render(); [...document.querySelectorAll('[data-col="done"] .card')].map(x => x.dataset.id)`);
res.sameCol = await ev(`moveTask('p0', 'prog', null); render(); [...document.querySelectorAll('[data-col="prog"] .card')].map(x => x.dataset.id)`);
res.errors = errors;
console.log(JSON.stringify(res));
quit(0);
"""

fails = passes = 0


def case(name, ok, detail=""):
    global fails, passes
    fails += not ok
    passes += bool(ok)
    print(("PASS" if ok else "FAIL") + f" {name}" + ("" if ok else f": {detail}"))


def main():
    port, dport = free_port(), free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    driver = os.path.join(TMP, "driver.mjs")
    open(driver, "w").write(DRIVER)
    try:
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome"),
                            str(W), str(H)], capture_output=True, text=True, timeout=120)
    finally:
        srv.shutdown()
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        print("FAIL driver gave no result\nstdout:", p.stdout[-2000:], "\nstderr:", p.stderr[-2000:])
        print("VERDICT: FAIL")
        return 1
    if "error" in res:
        print("FAIL", res["error"])
        print("VERDICT: FAIL")
        return 1

    g = res["geom"]
    case("setup: board rendered, In Progress column scrolls with a classic scrollbar", res["ready"] and g["max"] > 300 and g["sbw"] > 0, g)
    d = res["drag"]
    case("setup: polls kept coming during the drag (redraws after release)", res["dragRendersAfter"] >= 1, res["dragRendersAfter"])
    case("scrollbar drag: no redraw while the button is held", res["dragRendersDuring"] == 0, res["dragRendersDuring"])
    # Without the wait, the first redraw ends the drag and the column stops near 217px.
    case("scrollbar drag: kept moving down to the end of the drag", all(b > a for a, b in zip(d, d[1:])) and d[-1] > 400, d)
    case("scrollbar drag: position kept after the redraw on release", abs(res["afterDrag"] - d[-1]) <= 1, (res["afterDrag"], d[-1]))
    w = res["wheel"]
    case("wheel: no redraw while the wheel is turning", res["wheelRendersDuring"] == 0, res["wheelRendersDuring"])
    case("wheel: redraw happens once the wheel stops", res["wheelRendersAfter"] >= 1, res["wheelRendersAfter"])
    case("wheel: every tick scrolled up", all(b < a for a, b in zip([g["max"]] + w, w)), w)
    case("moved into In Progress: on top", res["toProg"][:1] == ["new"], res["toProg"][:3])
    case("moved into Done: on top", res["toDone"][:1] == ["p5"], res["toDone"])
    case("moved within its own column with no target: at the end", res["sameCol"][-1:] == ["p0"], res["sameCol"][-3:])
    case("no uncaught page exceptions", res["errors"] == [], res["errors"])

    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    print("VERDICT:", "FAIL" if fails else "PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
