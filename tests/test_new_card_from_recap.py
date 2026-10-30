"""The "Start new card from this" button on a done card with a recap. Drives the real index.html + i18n.js in
headless Chrome over raw CDP, with /api faked by a stub server on a free port (never the live board).

Run: python3 tests/test_new_card_from_recap.py  (stdlib + node 22+ + Google Chrome)."""
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
TMP = tempfile.mkdtemp(prefix="recap-card-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}
AT = "2026-10-30T10:00:00+00:00"
COLS = [{"id": "todo", "name": "To Do", "done": False},
        {"id": "rev", "name": "Review", "done": False, "role": "review"},
        {"id": "done", "name": "Done", "done": True}]
RECAP = {"from": "claude", "status": "recap", "at": AT, "message": "## Goal\nDo the thing."}
SAID = {"from": "claude", "status": "done", "at": AT, "message": "Done it"}
BTN = "Start new card from this"
# id: (column, agent fields, card's agent fields, the buttons expected in the Next action box)
CASES = {
    # A closed card reads finished + archived on the real board.
    "recap": ("done", {"phase": "finished", "archived": True, "closed": True, "log": [SAID, RECAP]}, {}, [BTN]),
    "writing": ("done", {"phase": "finished", "recapping": True, "closed": True, "log": [SAID]}, {}, []),
    "norecap": ("done", {"phase": "finished", "closed": True, "log": [SAID]}, {}, []),
    "working": ("done", {"phase": "working", "closed": True, "log": [SAID, RECAP]}, {}, []),
    "worktree": ("done", {"phase": "finished", "archived": True, "closed": True, "worktree": True, "log": [SAID, RECAP]},
                 {"worktree": {"path": "/x", "branch": "b"}}, None),
    "review": ("rev", {"phase": "finished", "log": [SAID, RECAP]}, {}, None),
}
TASKS, AGENTS = [], {}
for i, (tid, (col, ag, extra, _)) in enumerate(CASES.items()):
    TASKS.append({"id": tid, "title": f"Case {tid}", "notes": "", "priority": "", "tags": [], "column": col, "parent": None,
                  "order": i, "created": AT, "updated": AT, "agent": {"id": tid, "started": AT, **extra}})
    AGENTS[tid] = {"id": tid, "state": "idle", "reason": "", **ag}
spawned = []
NOTES = 'Continues the card "Case recap" (recap).\n\n## Goal\nDo the thing.'


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
            return self.send_json({"version": 1, "columns": COLS, "tasks": TASKS})
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
        if self.path.split("?")[0] == "/api/agent/spawn":
            spawned.append(json.loads(body))
            return self.send_json({"ok": True, "title": "Follow-up: Case recap", "tags": ["ui"], "notes": NOTES, "model": ""})
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
const wait = (ms) => new Promise(r => setTimeout(r, ms));
const shot = async (name) => writeFileSync(`${outdir}/${name}.png`, Buffer.from((await send('Page.captureScreenshot', { format: 'png' })).result.data, 'base64'));
await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url });
for (let i = 0; i < 100; i++) { await wait(100); if (await ev(`typeof polledOnce !== 'undefined' && polledOnce`) === true) break; }
await wait(300);
const out = { buttons: {} };
for (const k of JSON.parse(process.env.CASE_IDS)) {
  await ev(`openDetail(${JSON.stringify(k)})`);
  await wait(150);
  out.buttons[k] = await ev(`[...document.querySelectorAll('#d-next-actions button')].map((b) => b.textContent.trim())`);
}
await ev(`openDetail('recap')`);
await wait(150);
out.cls = await ev(`document.querySelector('#d-next-actions button').className`);
await shot('done-card');
await ev(`document.querySelector('#d-next-actions button').click()`);
await wait(100);
out.modal = await ev(`({ open: document.querySelector('#fresh-modal').classList.contains('open'),
  intro: document.querySelector('#fresh-intro').textContent,
  closeShown: getComputedStyle(document.querySelector('#fresh-close').closest('label')).display !== 'none',
  closeChecked: document.querySelector('#fresh-close').checked })`);
await ev(`document.querySelector('#fresh-text').value = 'Now add sound'`);
await shot('dialog');
await ev(`document.querySelector('#fresh-yes').click()`);
for (let i = 0; i < 50; i++) { await wait(100); if (await ev(`!document.querySelector('#fresh-modal').classList.contains('open')`) === true) break; }
await wait(200);
out.made = await ev(`(() => { const n = state.tasks.find((x) => x.title === 'Follow-up: Case recap'); return n && { notes: n.notes, tags: n.tags, column: n.column, open: openId === n.id }; })()`);
await shot('new-card');
// The ordinary dialog still offers closing the old session once the recap one was shown.
await ev(`openFresh(state.tasks.find((x) => x.id === 'review'))`);
await wait(100);
out.plain = await ev(`({ intro: document.querySelector('#fresh-intro').textContent,
  closeShown: getComputedStyle(document.querySelector('#fresh-close').closest('label')).display !== 'none' })`);
out.errors = errors;
console.log(JSON.stringify(out));
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
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome"), TMP],
                           capture_output=True, text=True, timeout=90, env=dict(os.environ, CASE_IDS=json.dumps(list(CASES))))
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
    for tid, (_, _, _, want) in CASES.items():
        if want is not None:
            check(f"{tid}: buttons", res["buttons"][tid], want)
    check("worktree card: button sits after the merge button", res["buttons"]["worktree"][-1:], [BTN])
    check("worktree card: merge button kept", len(res["buttons"]["worktree"]), 2)
    check("a card not in done gets no such button", BTN in res["buttons"]["review"], False)
    check("no merge waiting: the button is the main one", "primary" in res["cls"], True)
    check("dialog", res["modal"], {"open": True, "intro": "The new card starts with this card's recap.",
                                   "closeShown": False, "closeChecked": False})
    check("asks for the card's recap, closing nothing", spawned, [{"p": "demo", "taskId": "recap", "close": False}])
    check("new card holds the typed text and the recap",
          res["made"], {"notes": "Now add sound\n\n" + NOTES, "tags": ["ui"], "column": "todo", "open": True})
    check("ordinary dialog unchanged", res["plain"], {
        "intro": "Claude writes a short summary of this card's work, and a new card starts from it.", "closeShown": True})
    print(f"\n{passes} passed, {fails} failed  (screenshots in {TMP})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
