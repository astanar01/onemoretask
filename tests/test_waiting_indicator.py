"""The "waiting on you" signs: the needs-you outline on cards and list rows, the "Waiting for you" chip,
the top-bar #waiting count and the tab title. Drives the real index.html + i18n.js in headless Chrome over raw
CDP, with /api faked by a stub server on a free port (never the live board, never .task_board).

Run: python3 tests/test_waiting_indicator.py  (stdlib + node 22+ + Google Chrome)."""
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
TMP = tempfile.mkdtemp(prefix="waiting-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}

# Columns with roles, like a real board.
COLS = [{"id": "todo", "name": "To Do", "done": False},
        {"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "rev", "name": "Review", "done": False, "role": "review"},
        {"id": "done", "name": "Done", "done": True}]
OLD, NEW = "2026-10-15T10:00:00+00:00", "2026-10-15T12:00:00+00:00"   # card edits are newer than the logs


def entry(frm, msg, status="reply"):
    return {"from": frm, "status": status if frm == "you" else "done", "at": OLD, "message": msg}


# id: (column, phase or None, last log entry from, expected needs-you, expected chip)
CASES = {
    "c1needs": ("prog", "needs_you", "claude", True, False),
    "c2fin": ("rev", "finished", "claude", True, True),
    "c3youlast": ("rev", "finished", "you", False, False),
    "c4working": ("prog", "working", "claude", False, False),
    "c5done": ("done", "finished", "claude", False, False),
    "c6gone": ("rev", "gone", "claude", True, True),
    "c7noagent": ("todo", None, None, False, False),
}
TASKS, AGENTS = [], {}
for i, (tid, (col, phase, last, _, _)) in enumerate(CASES.items()):
    t = {"id": tid, "title": f"Case {tid}", "notes": "", "priority": "", "tags": [], "column": col, "parent": None,
         "order": i, "created": OLD, "updated": NEW}
    if phase:
        t["agent"] = {"id": tid, "started": OLD}
        log = [entry("you", "Do the thing", "launch"), entry("claude", "Done it")]
        if last == "you":
            log.append(entry("you", "One more change please"))
        AGENTS[tid] = {"id": tid, "state": "idle", "phase": phase, "reason": "", "log": log}
    TASKS.append(t)
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
            return self.send_json(AGENTS)
        if p == "/api/app-copy":   # {} would throw in checkAppCopy (a stub error, not the feature's)
            return self.send_json({"current": ROOT, "copies": [{"path": ROOT, "main": True}]})
        if p.startswith("/api/"):
            return self.send_json({})
        if p in SERVED:   # only the page and its script, read fresh from the checkout
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
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


DRIVER = r"""
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
const [url, chrome, dport, profile, outdir] = process.argv.slice(2);
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
await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 900, deviceScaleFactor: 1, mobile: false });
const MEASURE = `(() => {
  var ids = ${JSON.stringify(JSON.parse(process.env.CASE_IDS))};
  var out = { title: document.title, waiting: document.querySelector('#waiting').textContent,
    waitingShown: getComputedStyle(document.querySelector('#waiting')).display !== 'none',
    listMode: document.body.classList.contains('view-list'), theme: document.documentElement.dataset.theme || '',
    innerWidth, cards: {} };
  for (var k of ids) {
    var el = document.querySelector('.card[data-id="' + k + '"], .lcard[data-id="' + k + '"]');
    var task = state.tasks.find((x) => x.id === k);
    out.cards[k] = { found: !!el, kind: el ? el.className.split(' ')[0] : null, needsYou: !!(el && el.classList.contains('needs-you')),
      chip: el ? el.querySelectorAll('.yours').length : 0, chipText: el && el.querySelector('.yours') ? el.querySelector('.yours').textContent : null,
      column: task && task.column, fn: task ? waitsOnYou(task) : null };
  }
  return out;
})()`;
const results = {};
for (const [name, view, theme] of [['board-light', 'board', 'light'], ['board-dark', 'board', 'dark'], ['list-light', 'list', 'light']]) {
  const init = await send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('task-board:view', '${view}'); localStorage.setItem('task-board:theme', '${theme}'); } catch (e) {}` });
  await send('Page.navigate', { url });
  let r = null;
  // Wait for the first agents poll: the count and card classes only exist after it.
  for (let i = 0; i < 100; i++) {
    await new Promise(res => setTimeout(res, 100));
    r = await ev(`typeof polledOnce !== 'undefined' && polledOnce && document.querySelectorAll('${view === 'list' ? '.lcard' : '.card'}').length > 0`);
    if (r === true) break;
  }
  await new Promise(res => setTimeout(res, 400));
  results[name] = { ready: r === true, ...(await ev(MEASURE)) };
  if (view === 'board') {
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    const path = `${outdir}/waiting-${name}.png`;
    writeFileSync(path, Buffer.from(shot.result.data, 'base64'));
    results[name].shot = path;
  }
  await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: init.result.identifier });
}
results.errors = errors;
console.log(JSON.stringify(results));
quit(0);
"""

fails = passes = 0


def check(name, got, want):
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
    env = dict(os.environ, CASE_IDS=json.dumps(list(CASES)))
    try:
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome"), TMP],
                           capture_output=True, text=True, timeout=90, env=env)
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

    check("no page errors", res.pop("errors"), [])
    check("no board saves (stub data stayed as set up)", [c for c in calls if c.startswith("PUT")], [])
    for name in ("board-light", "board-dark", "list-light"):
        r = res[name]
        kind = "lcard" if name.startswith("list") else "card"
        check(f"{name}: page loaded and polled agents", r["ready"], True)
        check(f"{name}: layout is {'list' if kind == 'lcard' else 'board'}", r["listMode"], kind == "lcard")
        check(f"{name}: theme forced", r["theme"], name.split("-")[1])
        check(f"{name}: #waiting says 3", (r["waitingShown"], r["waiting"]), (True, "3 waiting on you"))
        check(f"{name}: title starts with (3)", r["title"].startswith("(3) "), True)
        for tid, (col, phase, last, want_nyou, want_chip) in CASES.items():
            c = r["cards"][tid]
            # Negative control: a missing card would read as "no class, no chip" and pass the "neither" cases.
            check(f"{name}: {tid} renders as .{kind} in column {col}", (c["found"], c["kind"], c.get("column")), (True, kind, col))
            check(f"{name}: {tid} waitsOnYou()", c["fn"], want_nyou)
            check(f"{name}: {tid} needs-you class", c["needsYou"], want_nyou)
            check(f"{name}: {tid} chip", (c["chip"], c["chipText"]), (1, "Waiting for you") if want_chip else (0, None))
    print("screenshots:", res["board-light"].get("shot"), res["board-dark"].get("shot"))
    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
