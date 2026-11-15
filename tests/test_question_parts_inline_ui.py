"""A many-part question's choices show under each part's own paragraph, not all at the end of the message.

When a question's message has one numbered paragraph per --part ("**2. Module page.** …"), the card cuts the message
there: the intro on top, then each paragraph with that part's choices right under it. The part's question line is
left out when the paragraph already starts with it. No paragraph for some part: the old layout (whole message, then
every row). Drives the real index.html + i18n.js in headless Chrome over raw CDP, with /api faked by a stub server on
a free port (never the live board, never .task_board).

Run: python3 tests/test_question_parts_inline_ui.py  (stdlib + node 22+ + Google Chrome).
INDEX=<file> serves another index.html (e.g. the previous commit's, to see the test fail on the old page).
SHOT=<file.png> saves a screenshot of the first card's question."""
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
TMP = tempfile.mkdtemp(prefix="question-parts-inline-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
INDEX = os.environ.get("INDEX") or os.path.join(ROOT, "index.html")
SHOT = os.environ.get("SHOT", "")
SERVED = {"/": INDEX, "/index.html": INDEX, "/i18n.js": os.path.join(ROOT, "i18n.js")}
OLD = "2026-11-11T09:00:00+00:00"
LATER = "2026-11-11T09:05:00+00:00"
YN = ["approved", "change it"]

# c1: the screenshot's shape — intro, then "**N. Title.** text" paragraphs, parts named like the titles.
Q1 = {"from": "claude", "status": "question", "at": OLD,
      "message": "Here are the screens to approve.\n\n"
                 "**1. Back-office layout and home.** A side menu and a home with three tiles.\n\n"
                 "**2. Module page.** One row per module with its own save button.\n\n"
                 "**3. Tester-mark page.** One form: the member's email and two buttons.",
      "parts": [{"q": "1. Back-office layout and home", "options": YN},
                {"q": "2. Module page", "options": YN},
                {"q": "3. Tester-mark page", "options": YN}]}
# c2: paragraphs numbered like the parts, but the parts ask their own question; and a code fence with "1." inside.
Q2 = {"from": "claude", "status": "question", "at": OLD,
      "message": "Two things.\n\n```\n1. not a heading\n```\n\n1. The tables are 2 m wide now.\n\n2. The chairs are red.",
      "parts": [{"q": "1. Keep the tables?", "options": YN}, {"q": "2. Keep the chairs?", "options": YN}]}
# c3: no numbered paragraphs: whole message, then every row (as before).
Q3 = {"from": "claude", "status": "question", "at": OLD, "message": "Two things to settle before I resize:",
      "parts": [{"q": "1. Tables?", "options": YN}, {"q": "2. Chairs?", "options": YN}]}
# c4: Q1 answered: still under each paragraph, picks marked.
A4 = {"from": "you", "status": "reply", "at": LATER,
      "message": "1. Back-office layout and home → approved\n2. Module page → change it\n3. Tester-mark page → approved"}
COLS = [{"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "done", "name": "Done", "done": True}]


def card(cid, k):
    return {"id": cid, "title": f"Card {cid}", "notes": "", "priority": "", "tags": [], "column": "prog", "parent": None,
            "order": k, "created": OLD, "updated": OLD, "agent": {"id": f"a-{cid}", "started": OLD}}


LOGS = {"c1": [Q1], "c2": [Q2], "c3": [Q3], "c4": [Q1, A4]}
AGENTS = {c: {"id": f"a-{c}", "sessionId": f"s-{c}", "state": "idle", "phase": "needs_you", "reason": "", "log": log,
              "mcp_uses": [], "mcp": None} for c, log in LOGS.items()}
BOARD = {"version": 1, "columns": COLS, "tasks": [card(c, k) for k, c in enumerate(LOGS)]}
SENT = []


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
        if p in SERVED:
            return self.send_bytes(open(SERVED[p], "rb").read(),
                                   "text/javascript" if p == "/i18n.js" else "text/html; charset=utf-8")
        self.send_bytes(b"", "text/plain", 404)

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if "/reply" in self.path or "/agent" in self.path:
            SENT.append(body.decode("utf-8", "replace"))
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
// The question's layout, top to bottom: each block is ["msg", text] / ["q", question line] / ["opts", labels].
// Message text is compared without spaces, first 24 characters (a list number and its text sit in separate spans).
// Block order in the DOM and on screen must agree (each block's top below the previous one's).
const layout = `(() => { const li = document.querySelector('#d-log li.k-question'); const out = []; let lastTop = -1, inOrder = true;
  li.querySelectorAll('.msg, .qpq, .qopts').forEach((n) => { const r = n.getBoundingClientRect(); if (r.top < lastTop) inOrder = false; lastTop = r.top;
    out.push(n.matches('.msg') ? ['msg', n.textContent.replace(/\\s+/g, '').slice(0, 24)] : n.matches('.qpq') ? ['q', n.textContent.trim()]
      : ['opts', [...n.querySelectorAll('button')].map((b) => b.textContent.trim())]); });
  return { out, inOrder }; })()`;
for (const c of ['c1', 'c2', 'c3', 'c4']) {
  await ev(`openDetail('${c}')`); await ev(`showTab('conversation')`); await sleep(400);
  res[c] = await ev(layout);
  if (c === 'c1' && shot) {
    const r = await ev(`(() => { const b = $('#d-log li.k-question').getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; })()`);
    const s = await send('Page.captureScreenshot', { format: 'png', clip: { x: Math.max(0, r.x - 8), y: Math.max(0, r.y - 8), width: r.w + 16, height: r.h + 16, scale: 1 } });
    writeFileSync(shot, Buffer.from(s.result.data, 'base64'));
  }
  if (c === 'c1') {
    // Pick a choice in each row: the counter and Send answers still follow the picks.
    const btns = `[...document.querySelectorAll('#d-log .qpart .qopts')]`;
    await ev(`${btns}[0].querySelectorAll('button')[0].click()`);
    await ev(`${btns}[1].querySelectorAll('button')[1].click()`);
    res.c1two = await ev(`[document.querySelector('[data-qsend]').disabled, document.querySelector('.qsend span').textContent]`);
    await ev(`${btns}[2].querySelectorAll('button')[0].click()`);
    res.c1three = await ev(`[document.querySelector('[data-qsend]').disabled, document.querySelector('.qsend span').textContent]`);
    res.c1picked = await ev(`${btns}.map((o) => [...o.querySelectorAll('button')].findIndex((b) => b.classList.contains('picked')))`);
  }
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


def m(text):
    return ["msg", text.replace(" ", "")[:24]]


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
    opts = ["opts", YN]
    check("page loaded and polled", res["ready"])
    check("no page errors", res["errors"], [])
    check("each part's choices sit right under its own paragraph (no repeated title)", res["c1"]["out"],
          [m("Here are the screens to approve."),
           m("1. Back-office layout and home. A side "), opts,
           m("2. Module page. One row per module with"), opts,
           m("3. Tester-mark page. One form: the membe"), opts])
    check("...and in that order on screen", res["c1"]["inOrder"])
    check("two of three picked: Send answers stays off", res["c1two"], [True, "2 of 3 answered"])
    check("three of three picked: Send answers turns on", res["c1three"], [False, "3 of 3 answered"])
    check("each row keeps its own pick", res["c1picked"], [0, 1, 0])
    check("a part asking its own question keeps its question line; a numbered line in code is not a paragraph",
          res["c2"]["out"],
          [m("Two things.1. not a heading"),
           m("1. The tables are 2 m wide now."), ["q", "1. Keep the tables?"], opts,
           m("2. The chairs are red."), ["q", "2. Keep the chairs?"], opts])
    check("no numbered paragraphs: message on top, every row after it (as before)", res["c3"]["out"],
          [m("Two things to settle before I resize:"),
           ["q", "1. Tables?"], opts, ["q", "2. Chairs?"], opts])
    check("an answered question stays under its paragraphs, picks marked", res["c4"]["out"],
          [m("Here are the screens to approve."),
           m("1. Back-office layout and home. A side "), ["opts", ["✓ approved", "change it"]],
           m("2. Module page. One row per module with"), ["opts", ["approved", "✓ change it"]],
           m("3. Tester-mark page. One form: the membe"), ["opts", ["✓ approved", "change it"]]])
    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
