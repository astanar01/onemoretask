"""A session waiting on the MCP server approval shows on its card as "found new tools in this project" with three
buttons (Use it / Use them, Always use new ones, Don't use) instead of the tool-permission Allow / Deny; a click posts
the choice to /api/agent/permission with the ask the card showed. Drives the real index.html + i18n.js in headless
Chrome over raw CDP, with /api faked by a stub server on a free port (never the live board, never .task_board).

Run: python3 tests/test_mcp_approval_ui.py  (stdlib + node 22+ + Google Chrome). SHOT=<file.png> keeps the screenshot."""
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
TMP = tempfile.mkdtemp(prefix="mcp-approval-ui-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SHOT = os.environ.get("SHOT") or os.path.join(TMP, "mcp-card.png")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}
ASK = "Claude asks permission: use new MCP servers: "
OLD, NEW = "2026-11-14T10:00:00+00:00", "2026-11-14T12:00:00+00:00"
COLS = [{"id": "todo", "name": "To Do", "done": False}, {"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "done", "name": "Done", "done": True}]
CASES = {"many": "laravel-boost, herd", "one": "laravel-boost", "tool": None}
TASKS, AGENTS = [], {}
for i, (tid, names) in enumerate(CASES.items()):
    TASKS.append({"id": tid, "title": f"Case {tid}", "notes": "", "priority": "", "tags": [], "column": "prog", "parent": None,
                  "order": i, "created": OLD, "updated": NEW, "agent": {"id": tid, "started": OLD}})
    perm = ASK + names if names else "Claude asks permission: Bash: rm -rf build"
    AGENTS[tid] = {"id": tid, "state": "blocked", "phase": "needs_you", "reason": perm, "permission": perm,
                   "log": [{"from": "you", "status": "launch", "at": OLD, "message": "Do the thing"}]}
BOARD = {"version": 1, "columns": COLS, "tasks": TASKS}
posts = []


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

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        p = self.path.split("?")[0]
        if p == "/api/agent/permission":
            posts.append(json.loads(body))
            return self.send_json({"next": "", "again": False, "once": False, "mcp": True})
        return self.send_json({"ok": True})

    do_PUT = do_POST


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
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('task-board:theme', 'light'); } catch (e) {}` });
await send('Page.navigate', { url });
for (let i = 0; i < 100; i++) { await sleep(100); if (await ev(`typeof polledOnce !== 'undefined' && polledOnce`) === true) break; }
const READ = `({ title: document.querySelector('#d-next-title').textContent, text: document.querySelector('#d-next-text').textContent,
  buttons: [...document.querySelectorAll('#d-next-actions button')].map(b => b.textContent.trim()) })`;
const out = {};
for (const k of ['tool', 'one', 'many']) {
  await ev(`openDetail('${k}')`); await sleep(500);
  out[k] = await ev(READ);
}
const shotData = await send('Page.captureScreenshot', { format: 'png' });
writeFileSync(shot, Buffer.from(shotData.result.data, 'base64'));
await ev(`[...document.querySelectorAll('#d-next-actions button')].find(b => b.textContent.trim() === 'Always use new ones').click()`);
for (let i = 0; i < 30; i++) { await sleep(100); if (/Saved your choice/.test(await ev(`document.body.textContent`))) break; }
out.status = await ev(`/Saved your choice\. Claude starts now\./.test(document.body.textContent)`);
out.errors = errors;
console.log(JSON.stringify(out));
quit(0);
"""

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
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
    check("no page errors", res["errors"], [])
    # Negative control: a tool permission keeps its Allow / Allow always / Deny box.
    check("tool permission: old box", (res["tool"]["title"], res["tool"]["buttons"]),
          ("Claude asks permission", ["Allow", "Allow always", "Deny"]))
    check("one server: title", res["one"]["title"], "Claude found new tools in this project")
    check("one server: text names it", "The project adds an MCP server: laravel-boost." in res["one"]["text"], True)
    check("one server: buttons", res["one"]["buttons"], ["Use it", "Always use new ones", "Don't use"])
    check("two servers: text names both", "The project adds MCP servers: laravel-boost, herd." in res["many"]["text"], True)
    check("two servers: buttons", res["many"]["buttons"], ["Use them", "Always use new ones", "Don't use"])
    check("click posts the choice with the ask the card showed",
          posts, [{"p": "demo", "taskId": "many", "choice": "always", "ask": ASK + "laravel-boost, herd"}])
    check("the answer is told in plain words", res["status"], True)
    print("screenshot:", SHOT)
    print("ALL PASS" if not fails else f"{fails} FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
