"""Notification sound: Chrome and Edge on macOS never sound their notifications, so the board plays its own chime
with each alert and marks the notification silent (so Windows does not ring twice). Drives the real index.html +
i18n.js in headless Chrome over raw CDP, with /api faked by a stub server (never the live board).

Run: python3 tests/test_notify_sound.py  (stdlib + node 22+ + Google Chrome)."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="notify-sound-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}
BOARD = {"version": 1, "columns": [{"id": "todo", "name": "To Do", "done": False}], "tasks": []}
ALERT = {"seq": 1, "project": "demo", "task": "t1", "title": "Claude needs you", "sub": "Case t1", "reason": ""}


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
        if p == "/api/alerts/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                time.sleep(0.5)
                self.wfile.write(b"event: alert\ndata: " + json.dumps(ALERT).encode() + b"\n\n")
                self.wfile.flush()
                time.sleep(3)
            except OSError:
                pass   # initNotify's probe hangs up at once
            return
        if p == "/api/projects":
            return self.send_json([{"id": "demo", "name": "demo", "path": TMP, "board": os.path.join(TMP, ".task_board")}])
        if p == "/api/tasks":
            return self.send_json(BOARD)
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


# Records every Notification and every oscillator start; the real constructors still run.
SPY = r"""
window.__notes = []; window.__tones = 0;
const RealN = window.Notification;
window.Notification = function (t, o) { window.__notes.push({ title: t, silent: !!(o && o.silent) }); return new RealN(t, o); };
Object.defineProperty(window.Notification, 'permission', { get: () => RealN.permission });
window.Notification.requestPermission = RealN.requestPermission.bind(RealN);
const realStart = OscillatorNode.prototype.start;
OscillatorNode.prototype.start = function (...a) { window.__tones++; return realStart.apply(this, a); };
"""

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
const bws = new WebSocket((await (await fetch(`http://127.0.0.1:${dport}/json/version`)).json()).webSocketDebuggerUrl);
await new Promise(r => bws.addEventListener('open', r, { once: true }));
await new Promise(r => { bws.addEventListener('message', r, { once: true });
  bws.send(JSON.stringify({ id: 1, method: 'Browser.grantPermissions', params: { origin: new URL(url).origin, permissions: ['notifications'] } })); });
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let id = 0; const pending = new Map(); const errors = [];
ws.addEventListener('message', ({ data }) => {
  const m = JSON.parse(data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  else if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
});
const send = (method, params = {}) => new Promise(r => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); return r.result.exceptionDetails ? { exc: r.result.exceptionDetails.exception?.description } : r.result.result.value; };
await send('Page.enable'); await send('Runtime.enable');
const spy = await send('Page.addScriptToEvaluateOnNewDocument', { source: process.env.SPY });
const results = {};
for (const [name, notify, sound] of [['sound-default', '1', null], ['sound-off', '1', '0'], ['notify-off', '0', null]]) {
  const set = `try { localStorage.clear(); localStorage.setItem('task-board:notify', '${notify}');` +
    (sound === null ? '' : ` localStorage.setItem('task-board:sound', '${sound}');`) + ` } catch (e) {}`;
  const init = await send('Page.addScriptToEvaluateOnNewDocument', { source: set });
  await send('Page.navigate', { url });
  await new Promise(res => setTimeout(res, 3000));   // page load + stream probe + the stub's alert
  results[name] = await ev(`({ notes: window.__notes, tones: window.__tones,
    perm: Notification.permission,
    rowShown: !document.querySelector('#notify-row').hidden,
    soundRowShown: !document.querySelector('#sound-row').hidden,
    soundChecked: document.querySelector('#notify-sound').checked })`);
  await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: init.result.identifier });
}
// Unticking then ticking the box saves the choice and plays a sample chime.
results.toggle = await ev(`(() => { const b = document.querySelector('#notify-sound'), t0 = window.__tones;
  b.click(); const off = localStorage.getItem('task-board:sound'), t1 = window.__tones;
  b.click(); return { off, offTones: t1 - t0, on: localStorage.getItem('task-board:sound'), onTones: window.__tones - t1 }; })()`);
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
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    driver = os.path.join(TMP, "driver.mjs")
    open(driver, "w").write(DRIVER)
    try:
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome")],
                           capture_output=True, text=True, timeout=90, env=dict(os.environ, SPY=SPY))
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
    d, off, no = res["sound-default"], res["sound-off"], res["notify-off"]
    check("permission granted to the page", d["perm"], "granted")
    check("sound on by default: one notification, marked silent", d["notes"], [{"title": ALERT["title"], "silent": True}])
    check("sound on by default: chime played (2 tones)", d["tones"], 2)
    check("sound on by default: sound row shown and ticked", (d["rowShown"], d["soundRowShown"], d["soundChecked"]), (True, True, True))
    # Negative control: the alert still arrives, without the chime, and the OS may sound it.
    check("sound off: notification arrives, not silent", off["notes"], [{"title": ALERT["title"], "silent": False}])
    check("sound off: no chime", off["tones"], 0)
    check("sound off: row shown, unticked", (off["soundRowShown"], off["soundChecked"]), (True, False))
    check("notifications off: no alert, no chime, sound row hidden", (no["notes"], no["tones"], no["soundRowShown"]), ([], 0, False))
    check("toggle: untick saves 0, no sample", (res["toggle"]["off"], res["toggle"]["offTones"]), ("0", 0))
    check("toggle: tick saves 1 and plays a sample", (res["toggle"]["on"], res["toggle"]["onTones"]), ("1", 2))
    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
