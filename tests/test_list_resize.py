"""List view: the #list-resize handle between the list pane (#board.list) and the docked panel (#overlay).
Drag sets --list-w (clamped), drag end saves task-board:listw, a saved value is applied on load, double-click
resets, ArrowLeft/ArrowRight step 16px, hidden in board view and under 641px. Drives the real index.html +
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
TMP = tempfile.mkdtemp(prefix="list-resize-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}

W, H = 1400, 900
MIN_W, MAX_W, DEFAULT_W = 200, 980, 240   # max = min(1400-360, 1400*0.7); default = clamp(240px, 17vw, 280px)

COLS = [{"id": "todo", "name": "To Do", "done": False},
        {"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "done", "name": "Done", "done": True}]
T0 = "2026-10-15T10:00:00+00:00"
TASKS = [{"id": f"t{i}", "title": f"Task {i}", "notes": "", "priority": "", "tags": [], "column": col, "parent": None,
          "order": i, "created": T0, "updated": T0} for i, col in enumerate(["todo", "todo", "prog", "done"])]
BOARD = {"version": 1, "columns": COLS, "tasks": TASKS}
calls = []


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
        calls.append("GET " + p)
        if p == "/api/projects":
            return self.send_json([{"id": "demo", "name": "demo", "path": TMP, "board": os.path.join(TMP, ".task_board")}])
        if p == "/api/tasks":
            return self.send_json(BOARD)
        if p == "/api/agents":
            return self.send_json({})
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
        calls.append(("PUT " if self.command == "PUT" else "POST ") + self.path.split("?")[0])
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
const MEASURE = `(() => {
  var h = document.querySelector('#list-resize'), b = document.querySelector('#board'), o = document.querySelector('#overlay');
  var hr = h ? h.getBoundingClientRect() : null, br = b.getBoundingClientRect(), or = o.getBoundingClientRect();
  return { innerWidth, listMode: document.body.classList.contains('view-list'), boardList: b.classList.contains('list'),
    hasHandle: !!h, shown: !!h && getComputedStyle(h).display !== 'none' && hr.width > 0 && hr.height > 0,
    hx: hr ? hr.left + hr.width / 2 : null, hy: hr ? hr.top + hr.height / 2 : null,
    role: h && h.getAttribute('role'), orient: h && h.getAttribute('aria-orientation'), tabindex: h && h.getAttribute('tabindex'),
    aria: h && h.getAttribute('aria-valuenow'), bw: br.width, bRight: br.right, oLeft: or.left,
    oShown: getComputedStyle(o).display !== 'none',
    inline: document.documentElement.style.getPropertyValue('--list-w'),
    ls: localStorage.getItem('task-board:listw'), focused: document.activeElement === h };
})()`;
const m = () => ev(MEASURE);
const mouse = (type, x, y, extra = {}) => send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1, ...extra });
const drag = async (toX) => {
  const s = await m();
  if (!s.shown) return { skipped: 'handle not shown', ...s };
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: s.hx, y: s.hy, button: 'none', buttons: 0 });
  await mouse('mousePressed', s.hx, s.hy);
  const steps = 8;
  for (let i = 1; i <= steps; i++) { await mouse('mouseMoved', s.hx + (toX - s.hx) * i / steps, s.hy); await sleep(16); }
  await mouse('mouseReleased', toX, s.hy);
  await sleep(200);
  return m();
};
const key = async (k, code, vk) => {
  await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk });
  await sleep(150);
  return m();
};

const res = {};
res.ready1 = await load('list', true);
res.default = await m();
res.drag500 = await drag(500);
res.clampLow = await drag(50);
res.clampHigh = await drag(1300);
res.readyReload = await load('list', false);
res.reload = await m();
// Keyboard from a mid value so +16 is not clamped.
res.drag600 = await drag(600);
await ev(`var lrh = document.querySelector('#list-resize'); if (lrh) lrh.focus(); true`);
res.right = await key('ArrowRight', 'ArrowRight', 39);
res.left = await key('ArrowLeft', 'ArrowLeft', 37);
// Saved value out of range is clamped on load.
await ev(`localStorage.setItem('task-board:listw', '5000'); true`);
res.readyBig = await load('list', false);
res.loadBig = await m();
// Double-click resets.
const s = await m();
if (s.shown) {
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: s.hx, y: s.hy, button: 'none', buttons: 0 });
  await mouse('mousePressed', s.hx, s.hy, { clickCount: 1 }); await mouse('mouseReleased', s.hx, s.hy, { clickCount: 1 });
  await mouse('mousePressed', s.hx, s.hy, { clickCount: 2 }); await mouse('mouseReleased', s.hx, s.hy, { clickCount: 2 });
  await sleep(200);
  res.reset = await m();
} else res.reset = { skipped: 'handle not shown', ...s };
res.readyBoard = await load('board', false);
res.boardView = await m();
await metrics(600);
res.readyNarrow = await load('list', false);
res.narrow = await m();
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


def near(a, b, tol=1):
    return isinstance(a, (int, float)) and abs(a - b) <= tol


def width_case(name, r, want, saved=True):
    """--list-w applied: #board width, #overlay left, aria-valuenow, and (optionally) the saved value."""
    if r.get("skipped"):
        return case(name, False, f"{r['skipped']} (is #list-resize implemented?)")
    got = {"board width": r["bw"], "overlay left": r["oLeft"], "aria-valuenow": r["aria"], "saved": r["ls"]}
    ok = near(r["bw"], want) and near(r["oLeft"], want) and r["aria"] == str(want)
    if saved:
        ok = ok and r["ls"] == str(want)
    case(f"{name} -> {want}px", ok, f"got {got}")


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

    d = res["default"]
    loaded = all(res[k] for k in ("ready1", "readyReload", "readyBig", "readyBoard", "readyNarrow"))
    case("setup: every load rendered the board", loaded,
         {k: res[k] for k in ("ready1", "readyReload", "readyBig", "readyBoard", "readyNarrow")})
    case("setup: list view at 1400px, overlay docked", (d["innerWidth"], d["listMode"], d["boardList"], d["oShown"]) == (W, True, True, True),
         f"innerWidth {d['innerWidth']}, view-list {d['listMode']}, #board.list {d['boardList']}, overlay shown {d['oShown']}")

    case("default: #board width 240", near(d["bw"], DEFAULT_W), f"got {d['bw']}")
    case("default: handle exists and is visible", d["hasHandle"] and d["shown"], f"exists {d['hasHandle']}, shown {d['shown']}")
    case("default: handle attrs role=separator aria-orientation=vertical tabindex=0",
         (d["role"], d["orient"], d["tabindex"]) == ("separator", "vertical", "0"), f"got {(d['role'], d['orient'], d['tabindex'])}")
    case("default: handle centre within 6px of #board right edge", d["hx"] is not None and abs(d["hx"] - d["bRight"]) <= 6,
         f"handle x {d['hx']}, board right {d['bRight']}")
    case("default: aria-valuenow 240", d["aria"] == str(DEFAULT_W), f"got {d['aria']!r}")
    case("default: nothing saved yet", d["ls"] is None, f"got {d['ls']!r}")

    width_case("drag to x=500", res["drag500"], 500)
    width_case("clamp low: drag to x=50", res["clampLow"], MIN_W)
    width_case("clamp high: drag to x=1300", res["clampHigh"], MAX_W)
    width_case("persistence: reload keeps last saved width", res["reload"], MAX_W)
    width_case("keyboard setup: drag to x=600", res["drag600"], 600)
    r = res["right"]
    case("keyboard: handle focused", r.get("focused") is True, f"activeElement is handle: {r.get('focused')}")
    width_case("keyboard: ArrowRight +16", r, 616)
    width_case("keyboard: ArrowLeft back", res["left"], 600)
    lb = res["loadBig"]
    case("load: saved 5000 is clamped to 980", near(lb["bw"], MAX_W) and lb["aria"] == str(MAX_W),
         f"board width {lb['bw']}, aria {lb['aria']!r}")
    rs = res["reset"]
    if rs.get("skipped"):
        case("reset: double-click", False, f"{rs['skipped']} (is #list-resize implemented?)")
    else:
        case("reset: double-click -> width 240, aria 240, inline --list-w gone, saved key gone",
             near(rs["bw"], DEFAULT_W) and rs["aria"] == str(DEFAULT_W) and rs["inline"] == "" and rs["ls"] is None,
             f"board width {rs['bw']}, aria {rs['aria']!r}, inline {rs['inline']!r}, saved {rs['ls']!r}")
    b = res["boardView"]
    case("board view: handle not displayed", b["listMode"] is False and b["shown"] is False and b["hasHandle"],
         f"view-list {b['listMode']}, handle exists {b['hasHandle']}, shown {b['shown']}")
    n = res["narrow"]
    case("narrow 600px list view: handle not displayed", n["innerWidth"] == 600 and n["listMode"] and n["hasHandle"] and n["shown"] is False,
         f"innerWidth {n['innerWidth']}, view-list {n['listMode']}, handle exists {n['hasHandle']}, shown {n['shown']}")
    case("no uncaught page exceptions", res["errors"] == [], f"{res['errors']}")
    case("no board saves sent to the stub", not [c for c in calls if not c.startswith("GET")],
         [c for c in calls if not c.startswith("GET")])

    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    print("VERDICT:", "FAIL" if fails else "PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
