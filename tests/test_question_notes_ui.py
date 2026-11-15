"""Every question on a card has an "Add a note" link under its choices; the note goes out with the answer.

A many-part question gets one link per part; a single question with --option buttons gets one under its buttons.
Clicking the link opens a note box. The note is sent on its own line ("Note: ...") right under that question's
pick. A poll's redraw keeps the box, its text and the caret. An answered question still shows its pick as chosen
when a note line follows it, and shows no link. An answer typed in the reply box (Send reply) carries the live
question's notes after a blank line; with an empty box the notes go alone. A part with no pick but a typed note
counts as answered (the count and Send answers follow the typing), and its note goes out as that part's answer.
Drives the real index.html + i18n.js in headless Chrome over raw CDP, with /api faked by a stub server on a free port
(never the live board, never .task_board). Each run uses a fresh mkdtemp folder.

Run: python3 tests/test_question_notes_ui.py  (stdlib + node 22+ + Google Chrome).
INDEX=<file> serves another index.html (e.g. the previous commit's, to see the test fail on the old page).
SHOT=<file.png> saves a screenshot of the many-part question with one part answered by its note alone."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from collections import defaultdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="question-notes-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
INDEX = os.environ.get("INDEX") or os.path.join(ROOT, "index.html")
SHOT = os.environ.get("SHOT", "")
SERVED = {"/": INDEX, "/index.html": INDEX, "/i18n.js": os.path.join(ROOT, "i18n.js")}
OLD = "2026-11-11T09:00:00+00:00"
LATER = "2026-11-11T09:05:00+00:00"

PARTS_Q = {"from": "claude", "status": "question", "at": OLD, "message": "Three things to settle:",
           "parts": [{"q": "1. Are the tables the right size?", "options": ["a) Yes", "b) No"]},
                     {"q": "2. And the chairs?", "options": ["a) Yes", "b) No"]},
                     {"q": "3. And the barrels?", "options": ["a) Yes", "b) No"]}]}
ONE_Q = {"from": "claude", "status": "question", "at": OLD, "message": "Keep the old menu?", "options": ["a) Yes", "b) No"]}
ANSWERED = {"from": "you", "status": "reply", "at": LATER, "message": "a) Yes\nNote: only on Mondays"}
COLS = [{"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "done", "name": "Done", "done": True}]


def card(cid, k):
    return {"id": cid, "title": f"Card {cid}", "notes": "", "priority": "", "tags": [], "column": "prog", "parent": None,
            "order": k, "created": OLD, "updated": OLD, "agent": {"id": f"a-{cid}", "started": OLD}}


def agent(cid, log):
    return {"id": f"a-{cid}", "sessionId": f"s-{cid}", "state": "idle", "phase": "needs_you", "reason": "",
            "log": log, "mcp_uses": [], "mcp": None}


AGENTS = {"c1": agent("c1", [PARTS_Q]), "c2": agent("c2", [ONE_Q]), "c3": agent("c3", [ONE_Q, ANSWERED]),
          "c4": agent("c4", [PARTS_Q]), "c5": agent("c5", [ONE_Q]), "c6": agent("c6", [ONE_Q]), "c7": agent("c7", [ONE_Q]),
          "c8": agent("c8", [PARTS_Q])}
BOARD = {"version": 1, "columns": COLS, "tasks": [card(c, k) for k, c in enumerate(["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"])]}
REPLIES = []


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
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.split("?")[0] == "/api/agent/reply":
            d = json.loads(body or b"{}")
            REPLIES.append({"taskId": d.get("taskId"), "text": d.get("text")})
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
const type = (sel, text) => ev(`(() => { const n = document.querySelector(${JSON.stringify(sel)}); n.value = ${JSON.stringify(text)}; n.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);

// Many-part question: one link per part, no box yet.
await ev(`openDetail('c1')`); await ev(`showTab('conversation')`); await sleep(400);
res.partLinks = await ev(`[...document.querySelectorAll('#d-log .qpart')].map((p) => p.querySelectorAll('.qnote-add').length)`);
res.linkText = await ev(`document.querySelector('#d-log .qnote-add')?.textContent`);
await ev(`document.querySelectorAll('#d-log .qpart')[1].querySelector('.qnote-add').click()`);
res.boxOpened = await ev(`[...document.querySelectorAll('#d-log .qpart')].map((p) => !!p.querySelector('textarea.qnote'))`);
res.boxFocused = await ev(`document.activeElement === document.querySelector('#d-log .qpart:nth-child(2) textarea.qnote')`);
await type('#d-log textarea.qnote', 'wobbly legs');
// A poll's redraw keeps the note, the focus and the caret.
await ev(`renderAgent(task('c1'))`); await sleep(100);
res.afterRedraw = await ev(`(() => { const n = document.querySelector('#d-log textarea.qnote'); return n && [n.value, document.activeElement === n, n.selectionStart]; })()`);
await ev(`document.querySelector('#d-log button[data-qp="0"][data-qo="0"]').click()`);
await ev(`document.querySelector('#d-log button[data-qp="1"][data-qo="1"]').click()`);
await ev(`document.querySelector('#d-log button[data-qp="2"][data-qo="0"]').click()`);
const capture = async () => {
  if (!shot) return;
  const r = await ev(`(() => { const b = $('#d-log li.k-question').getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; })()`);
  const s = await send('Page.captureScreenshot', { format: 'png', clip: { x: Math.max(0, r.x - 8), y: Math.max(0, r.y - 8), width: r.w + 16, height: r.h + 16, scale: 1 } });
  writeFileSync(shot, Buffer.from(s.result.data, 'base64'));
};
await ev(`document.querySelector('#d-log button[data-qsend]').click()`); await sleep(600);

// Single question with option buttons: one link; the note rides under the clicked option.
await ev(`openDetail('c2')`); await ev(`showTab('conversation')`); await sleep(400);
res.singleLinks = await ev(`document.querySelectorAll('#d-log li.k-question .qnote-add').length`);
await ev(`document.querySelector('#d-log .qnote-add').click()`);
await type('#d-log textarea.qnote', 'only on\nMondays');
await ev(`document.querySelector('#d-log button[data-qo="0"]').click()`); await sleep(600);

// Answered single question whose answer carries a note: the pick still shows as chosen, and no link.
await ev(`openDetail('c3')`); await ev(`showTab('conversation')`); await sleep(400);
res.answered = await ev(`[...document.querySelectorAll('#d-log li.k-question button[data-qo]')].map((b) => [b.textContent, b.classList.contains('picked')])`);
res.answeredLinks = await ev(`document.querySelectorAll('#d-log .qnote-add, #d-log textarea.qnote').length`);

// Typed answer in the reply box (Send reply button): notes under the live question go out with it.
const typedSend = async (cid, note, part, typed) => {
  await ev(`openDetail('${cid}')`); await ev(`showTab('conversation')`); await sleep(400);
  if (note) {
    await ev(part == null ? `document.querySelector('#d-log .qnote-add').click()`
      : `document.querySelectorAll('#d-log .qpart')[${part}].querySelector('.qnote-add').click()`);
    await type('#d-log textarea.qnote', note);
  }
  await type('#d-reply', typed);
  const disabled = await ev(`$('#d-reply-send').disabled`);
  if (disabled === false) await ev(`$('#d-reply-send').click()`);
  await sleep(600);
  return disabled;
};
res.sendDisabled = {
  c4: await typedSend('c4', 'wobbly legs', 1, 'My own answer'),
  c5: await typedSend('c5', 'keep my note', null, 'Use the blue one'),
  c6: await typedSend('c6', '', null, 'Just text'),
  c7: await typedSend('c7', 'only the note', null, ''),
};

// A part with no pick but a typed note counts as answered: the note is its answer.
await ev(`openDetail('c8')`); await ev(`showTab('conversation')`); await sleep(400);
const sendState = () => ev(`(() => { const s = document.querySelector('#d-log button[data-qsend]'); return [s.disabled, s.nextElementSibling.textContent]; })()`);
await ev(`document.querySelector('#d-log button[data-qp="0"][data-qo="0"]').click()`);
await ev(`document.querySelector('#d-log button[data-qp="2"][data-qo="1"]').click()`);
res.noteOnly = { picksOnly: await sendState() };
await ev(`document.querySelectorAll('#d-log .qpart')[1].querySelector('.qnote-add').click()`);
await type('#d-log textarea.qnote', '   ');
res.noteOnly.blank = await sendState();
await type('#d-log textarea.qnote', 'Only the\nbig ones');
res.noteOnly.typed = await sendState();
await ev(`renderAgent(task('c8'))`); await sleep(100);
res.noteOnly.redrawn = await sendState();
await type('#d-log textarea.qnote', '');
res.noteOnly.cleared = await sendState();
await type('#d-log textarea.qnote', 'Only the\nbig ones');
await capture();
await ev(`document.querySelector('#d-log button[data-qsend]').click()`); await sleep(600);
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
        res = defaultdict(lambda: None, json.loads(p.stdout.strip().splitlines()[-1]))   # an old page stops early
    except Exception:
        print("FAIL driver gave no result\nstdout:", p.stdout[-2000:], "\nstderr:", p.stderr[-2000:])
        return 1
    if "error" in res:
        print("FAIL", res["error"])
        return 1
    print("page:", INDEX)
    check("page loaded and polled", res["ready"])
    check("no page errors", res["errors"], [])
    check("each part of a many-part question has one Add a note link", res["partLinks"], [1, 1, 1])
    check("the link reads Add a note", res["linkText"], "+ Add a note")
    check("clicking a part's link opens a note box under that part only", res["boxOpened"], [False, True, False])
    check("the new note box has the focus", res["boxFocused"])
    check("a redraw keeps the note text, the focus and the caret", res["afterRedraw"], ["wobbly legs", True, 11])
    check("a single question has one Add a note link", res["singleLinks"], 1)
    check("the answers sent carry each note under its own question", [r for r in REPLIES if r["taskId"] in ("c1", "c2")], [
        {"taskId": "c1", "text": "1. Are the tables the right size? → a) Yes\n2. And the chairs? → b) No\n"
                                 "Note: wobbly legs\n3. And the barrels? → a) Yes"},
        {"taskId": "c2", "text": "a) Yes\nNote: only on Mondays"}])
    check("an answer with a note still marks its pick", res["answered"], [["✓ a) Yes", True], ["b) No", False]])
    check("an answered question shows no note link", res["answeredLinks"], 0)
    sent = lambda cid: [r["text"] for r in REPLIES if r["taskId"] == cid]
    check("the Send reply button was enabled for each typed answer", res["sendDisabled"],
          {"c4": False, "c5": False, "c6": False, "c7": False})
    check("typed answer on a many-part question carries the part's note", sent("c4"),
          ["My own answer\n\n2. And the chairs?\nNote: wobbly legs"])
    check("typed answer on a single question carries its note", sent("c5"), ["Use the blue one\n\nNote: keep my note"])
    check("typed answer with no note goes out unchanged", sent("c6"), ["Just text"])
    check("empty reply box with a note sends the note alone", sent("c7"), ["Note: only the note"])
    no = res["noteOnly"] or {}
    check("two of three parts picked: Send answers stays off", no.get("picksOnly"), [True, "2 of 3 answered"])
    check("a blank note does not count as an answer", no.get("blank"), [True, "2 of 3 answered"])
    check("typing a note on the unpicked part turns Send answers on", no.get("typed"), [False, "3 of 3 answered"])
    check("a redraw keeps the note counted", no.get("redrawn"), [False, "3 of 3 answered"])
    check("clearing the note turns Send answers off again", no.get("cleared"), [True, "2 of 3 answered"])
    check("the note is sent as that part's answer", sent("c8"), [
        "1. Are the tables the right size? → a) Yes\n2. And the chairs? → Only the big ones\n3. And the barrels? → b) No"])
    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
