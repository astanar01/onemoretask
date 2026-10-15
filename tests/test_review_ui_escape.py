"""Board data written to tasks.json by hand or by a session (a priority, a task or column id, a session id) must
show as text, never run as page markup. Drives the real index.html + i18n.js in headless Chrome over raw CDP,
with /api faked by a stub server on a free port (never the live board, never .task_board).

Run: python3 tests/test_review_ui_escape.py  (stdlib + node 22+ + Google Chrome)."""
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
TMP = tempfile.mkdtemp(prefix="escape-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}

# Each payload closes its attribute and adds an <img> whose onerror counts a hit.
X = lambda tag: f'{tag}"><img src=x onerror=__xss.push("{tag}")>'
OLD = "2026-10-15T10:00:00+00:00"
COLS = [{"id": "todo", "name": "To Do", "done": False},
        {"id": X("colid"), "name": "In Progress", "done": False, "role": "progress"},
        {"id": "rev", "name": "Review", "done": False, "role": "review"},
        {"id": "done", "name": "Done", "done": True}]
PARENT = X("parentid")
TASKS = [
    {"id": PARENT, "title": "Parent", "notes": "", "priority": X("prio"), "tags": [], "column": "todo", "parent": None,
     "order": 0, "created": OLD, "updated": OLD},
    {"id": "kid", "title": "Kid", "notes": "", "priority": "", "tags": [], "column": "rev", "parent": PARENT,
     "order": 1, "created": OLD, "updated": OLD, "agent": {"id": X("agentid"), "started": OLD}},
]
AGENTS = {"kid": {"id": "kid", "state": "idle", "phase": "finished", "reason": "",
                  "log": [{"from": "claude", "status": "done", "at": OLD, "message": "Done"}]}}
BOARD = {"version": 1, "columns": COLS, "tasks": TASKS}


class Stub(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
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
            return self.send_json(AGENTS)
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
let id = 0; const pending = new Map();
ws.addEventListener('message', ({ data }) => { const m = JSON.parse(data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
const send = (method, params = {}) => new Promise(r => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, replMode: true }); return r.result.exceptionDetails ? { exc: r.result.exceptionDetails.exception?.description } : r.result.result.value; };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
await send('Page.enable'); await send('Runtime.enable');
const results = {};
for (const view of ['board', 'list']) {
  const init = await send('Page.addScriptToEvaluateOnNewDocument', { source: `window.__xss = []; try { localStorage.setItem('task-board:view', '${view}'); } catch (e) {}` });
  await send('Page.navigate', { url });
  let ready = false;
  for (let i = 0; i < 100 && !ready; i++) { await sleep(100); ready = await ev(`typeof polledOnce !== 'undefined' && polledOnce && document.querySelectorAll('.card, .lcard').length === 2`) === true; }
  await sleep(300);
  // The panel shows the crumb (parent id), the column picker (column ids) and the agent line.
  await ev(`openDetail('kid')`);
  await sleep(500);
  results[view] = { ready, hits: await ev(`window.__xss.slice()`), imgs: await ev(`document.querySelectorAll('img[src="x"]').length`),
    crumb: await ev(`document.querySelector('#d-crumb a') && document.querySelector('#d-crumb a').dataset.id`),
    prio: await ev(`[...document.querySelectorAll('.prio')].map((e) => e.className)`) };
  await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: init.result.identifier });
}
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
    for view in ("board", "list"):
        r = res[view]
        check(f"{view}: page loaded with both cards", r["ready"], True)
        check(f"{view}: no injected markup ran", r["hits"], [])
        check(f"{view}: no injected <img>", r["imgs"], 0)
        # Positive controls: the payloads reached the page and stayed text.
        check(f"{view}: crumb keeps the parent id as data", r["crumb"], PARENT)
        check(f"{view}: priority stays inside the class", any(X("prio") in c for c in r["prio"]), True)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{passes} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
