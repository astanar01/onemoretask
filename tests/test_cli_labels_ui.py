"""Which CLI works on a card, and which wrote what: the status pill on the card face, the list row and the panel head
carries a dot in the CLI's color; each conversation message names the CLI that wrote it (the server's "cli" on the
entry: a Codex review on a Claude card reads as Codex); the Summary lists each CLI with what it did and its message
count. Drives the real index.html + i18n.js in headless Chrome over raw CDP, /api faked by a stub on a free port.

Run: python3 tests/test_cli_labels_ui.py  (stdlib + node 22+ + Google Chrome). SHOT_DIR=<dir> keeps the screenshots."""
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
TMP = tempfile.mkdtemp(prefix="cli-labels-test-")
SHOTS = os.environ.get("SHOT_DIR") or TMP
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}
OLD = "2026-11-23T09:00:00+00:00"


def card(tid, i, title, agent):
    return {"id": tid, "title": title, "notes": "", "priority": "", "tags": [], "column": "prog", "parent": None,
            "order": i, "created": OLD, "updated": OLD, "agent": {"id": tid, "started": OLD, **agent}}


def e(frm, status, msg, at, **kw):
    return {"from": frm, "status": status, "message": msg, "at": f"2026-11-23T09:{at:02d}:00+00:00", **kw}


TASKS = [card("mix", 0, "Claude did it, Codex reviewed", {"model": "opus"}),
         card("cx", 1, "Codex at work", {"model": "gpt-5.5", "cli": "codex"}),
         card("ag", 2, "Antigravity waits", {"model": "agy:gemini-3.8-flash", "cli": "antigravity"})]
MIX_LOG = [e("you", "launch", "Sent to Claude (session mix, model opus)", 1, session="mix", cli="claude"),
           e("claude", "progress", "Looking at the parser", 2, cli="claude"),
           e("claude", "done", "Parser fixed in abc1234", 3, cli="claude"),
           e("you", "review", "Code review started (gpt-5.5, session r1)", 4, session="r1", cli="codex"),
           e("claude", "done", "One finding: an empty file crashes", 5, cli="codex")]
AGENTS = {
    # The review is over: the card's session is the reviewer's, so the pill names Codex.
    "mix": {"id": "r1", "state": "idle", "phase": "finished", "reason": "", "log": MIX_LOG, "subagents": [], "cli": "codex"},
    "cx": {"id": "cx", "state": "busy", "phase": "working", "reason": "", "subagents": [], "cli": "codex",
           "log": [e("claude", "progress", "Reading the tests", 6, cli="codex")]},
    # An older server: no "cli" on the entries; the card's CLI is used.
    "ag": {"id": "ag", "state": "blocked", "phase": "needs_you", "reason": "", "subagents": [], "cli": "antigravity",
           "log": [e("claude", "question", "Which file?", 7)]},
}
BOARD = {"version": 1, "columns": [{"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
                                   {"id": "done", "name": "Done", "done": True}], "tasks": TASKS}


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
        if p == "/api/codex-models":
            return self.send_json({"models": [{"id": "gpt-5.5", "name": "GPT-5.5"}], "claude": True, "codex": True,
                                   "antigravity": True, "antigravity_models": [{"id": "agy:gemini-3.8-flash", "name": "Gemini 3.8 Flash"}]})
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
import { writeFileSync } from 'node:fs';
const [url, chrome, dport, profile, outdir] = process.argv.slice(2);
const proc = spawn(chrome, ['--headless=new', `--remote-debugging-port=${dport}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', 'about:blank'], { stdio: 'ignore' });
const quit = (code) => { try { proc.kill(); } catch {} setTimeout(() => process.exit(code), 300); };
process.on('SIGTERM', () => quit(3)); process.on('SIGINT', () => quit(3));
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
const sleep = (ms) => new Promise(res => setTimeout(res, ms));
const shot = async (name, sel, pad = 12) => {
  const r = await ev(`(() => { const b = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; })()`);
  const clip = { x: Math.max(0, r.x - pad), y: Math.max(0, r.y - pad), width: r.width + 2 * pad, height: r.height + 2 * pad, scale: 1 };
  const s = await send('Page.captureScreenshot', { format: 'png', clip, captureBeyondViewport: true });
  writeFileSync(`${outdir}/${name}`, Buffer.from(s.result.data, 'base64'));
};
await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1300, height: 1000, deviceScaleFactor: 2, mobile: false });
await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });
const init = await send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('task-board:lang', 'en'); localStorage.setItem('task-board:theme', 'light'); } catch (e) {}` });
await send('Page.navigate', { url });
let ready = null;
for (let i = 0; i < 100 && ready !== true; i++) {
  await sleep(100);
  ready = await ev(`typeof polledOnce !== 'undefined' && polledOnce && state.tasks.length > 0 && Object.keys(agents).length > 0`);
}
await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: init.result.identifier });
const res = { ready, errors };
res.face = await ev(`[...document.querySelectorAll('.card, .lcard')].map((c) => ({ id: c.dataset.id, dot: c.querySelector('.agent .cli-dot')?.className, pill: c.querySelector('.agent')?.textContent.trim() }))`);
await shot('cli-cards.png', '#board', 4);
for (const c of ['mix', 'cx', 'ag']) {
  await ev(`openDetail('${c}')`); await sleep(120);
  await ev(`mainTab = 'conversation'; showTab(); renderAgent(task('${c}'))`); await sleep(80);
  res[c] = { who: await ev(`[...document.querySelectorAll('#d-log li .who b.cli-name')].map((b) => b.className.replace('cli-name cli-', '') + ':' + b.textContent)`),
             head: await ev(`document.querySelector('#d-agent .cli-dot')?.className`) };
  if (c === 'mix') await shot('cli-conversation.png', '#d-log', 8);
  await ev(`mainTab = 'summary'; showTab(); renderSummary(task('${c}'))`); await sleep(80);
  res[c].chips = await ev(`[...document.querySelectorAll('#d-sum-result .cli-chip')].map((x) => [...x.children].map((k) => k.textContent).join(' '))`);
  res[c].label = await ev(`document.querySelector('#d-sum-result .sum-label')?.textContent`);
  res[c].review = await ev(`document.querySelector('#d-sum-verify .sum-label')?.textContent`);
  if (c === 'mix') await shot('cli-summary.png', '#d-sum-result', 8);
}
await ev(`closeDetail()`); await sleep(100);
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
    os.makedirs(SHOTS, exist_ok=True)
    try:
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome"), SHOTS],
                           capture_output=True, text=True, timeout=120)
    finally:
        srv.shutdown()
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        print("FAIL driver gave no result\nstdout:", p.stdout[-2000:], "\nstderr:", p.stderr[-2000:])
        return 1
    check("page ready", res.get("ready"))
    check("no page errors", res.get("errors"), [])
    face = {f["id"]: f for f in res.get("face") or []}
    check("mix pill: Codex dot (the reviewer's session)", (face.get("mix") or {}).get("dot"), "cli-dot cli-codex")
    check("codex pill: Codex dot", (face.get("cx") or {}).get("dot"), "cli-dot cli-codex")
    check("antigravity pill: Antigravity dot", (face.get("ag") or {}).get("dot"), "cli-dot cli-antigravity")
    check("antigravity pill text", (face.get("ag") or {}).get("pill", "").startswith("Antigravity:"))
    mix = res.get("mix") or {}
    check("mix conversation names each writer", mix.get("who"), ["claude:Claude", "claude:Claude", "codex:Codex"])
    check("mix summary chips", mix.get("chips"), ["Claude the task 2 messages", "Codex code review 1 message"])
    check("mix result is Claude's", "Claude" in (mix.get("label") or ""))
    check("mix review names Codex", "Codex" in (mix.get("review") or ""))
    check("codex conversation", (res.get("cx") or {}).get("who"), ["codex:Codex"])
    check("codex panel head dot", (res.get("cx") or {}).get("head"), "cli-dot cli-codex")
    check("old entries fall back to the card's CLI", (res.get("ag") or {}).get("who"), ["antigravity:Antigravity"])
    check("antigravity chip without a launch line", (res.get("ag") or {}).get("chips"), ["Antigravity 1 message"])
    print(f"{passes} passed, {fails} failed  (screenshots in {SHOTS})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
