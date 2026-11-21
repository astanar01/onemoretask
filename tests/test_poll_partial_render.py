"""Board polls redraw only what changed. A poll that brings new steps for one card (the server's change token lists
only that card) leaves every other card element in place, in the board view and in the list view, replaces only the
changed card, and adds rows to the open card's step list instead of rebuilding the conversation. The reply box keeps
its text, focus and cursor. A poll that moves a card (its session finished) still redraws the whole board, and a full
redraw waits while someone is typing. Drives the real index.html + i18n.js in headless Chrome over raw CDP, with /api
faked by a stub server on a free port (never the live board, never .task_board).

Run: python3 tests/test_poll_partial_render.py  (stdlib + node 22+ + Google Chrome)."""
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
ROOT = os.environ.get("BOARD_ROOT") or os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="poll-partial-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}

COLS = [{"id": "todo", "name": "To Do", "done": False},
        {"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "rev", "name": "Review", "done": False, "role": "review"},
        {"id": "done", "name": "Done", "done": True}]
T0 = "2026-11-20T10:00:00+00:00"
IDS = ["a", "b", "c"]
TASKS = [{"id": i, "title": f"Card {i}", "notes": "", "priority": "", "tags": [], "column": "prog", "parent": None,
          "order": n, "created": T0, "updated": T0, "agent": {"session": f"s{i}", "id": f"s{i}"}} for n, i in enumerate(IDS)]
BOARD = {"version": 1, "columns": COLS, "tasks": TASKS}
AT = 1795168800   # 2026-11-20T10:00:00Z, the time of the task message: steps after it are the current run
LOG = [{"from": "you", "status": "task", "message": "Do it", "at": T0},
       {"from": "claude", "status": "progress", "message": "Started", "at": "2026-11-20T10:00:05+00:00"}]

lock = threading.Lock()
state = {"rev": 1, "steps": 2, "phase_a": "working", "hist": []}   # hist: (rev, card ids changed at that rev)


def bump(ids):
    state["rev"] += 1
    state["hist"].append((state["rev"], set(ids)))


def agent(cid):
    if cid != "a":
        return {"phase": "working", "log": list(LOG), "mcp_uses": [{"id": f"{cid}1", "at": AT + 10, "kind": "tool", "tool": "Read", "what": "x.py", "state": "done"}]}
    uses = [{"id": f"a{k}", "at": AT + 10 + k, "kind": "tool", "tool": "Bash", "what": f"step {k}", "state": "done"} for k in range(state["steps"])]
    return {"phase": state["phase_a"], "log": list(LOG), "mcp_uses": uses}


class Stub(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Board-Rev", "1")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p, _, q = self.path.partition("?")
        if p == "/api/projects":
            return self.send_json([{"id": "demo", "name": "demo", "path": TMP, "board": os.path.join(TMP, ".task_board")}])
        if p == "/api/tasks":
            return self.send_json(BOARD)
        if p == "/api/agents":
            # Like the server: with a token it knows, only the cards changed since then come back.
            since = dict(x.split("=", 1) for x in q.split("&") if "=" in x).get("since", "")
            with lock:
                known = since.isdigit() and (int(since) == state["rev"] or bool(state["hist"]) and state["hist"][0][0] - 1 <= int(since) <= state["rev"])
                ids = sorted(set().union(*[c for r, c in state["hist"] if r > int(since)])) if known else IDS
                return self.send_json({"ids": IDS, "cards": {i: agent(i) for i in ids}}, headers={"X-Agents-Rev": str(state["rev"])})
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
        p = self.path.split("?")[0]
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with lock:
            if p == "/__step":     # card a's session takes one more step
                state["steps"] += 1
                bump({"a"})
            elif p == "/__finish":  # card a's session finishes: it moves to Review
                state["phase_a"] = "finished"
                bump({"a"})
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
const [url, chrome, dport, profile] = process.argv.slice(2);
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
await send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 800, deviceScaleFactor: 1, mobile: false });

let initId = null;
const load = async (view) => {
  if (initId) await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: initId });
  initId = (await send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('task-board:view', '${view}'); localStorage.setItem('task-board:theme', 'light'); } catch (e) {}` })).result.identifier;
  await send('Page.navigate', { url });
  let r = null;
  for (let i = 0; i < 100; i++) { await sleep(100); r = await ev(`typeof polledOnce !== 'undefined' && polledOnce && document.querySelectorAll('.lcard, .card').length >= 3`); if (r === true) break; }
  // The test drives the polls itself.
  await ev(`clearTimeout(pollTimer); pollAgents = ((f) => async function () { const r = await f.apply(this, arguments); clearTimeout(pollTimer); return r; })(pollAgents); true`);
  await ev(`window.__r = 0; { const _r = render; render = function () { window.__r++; return _r.apply(this, arguments); }; } true`);
  return r === true;
};
const sel = (v) => v === 'list' ? '.lcard' : '.card';
const tagCards = (v) => ev(`document.querySelectorAll('#board ${sel(v)}').forEach((el) => el.__tag = el.dataset.id); true`);
const cards = (v) => ev(`Object.fromEntries([...document.querySelectorAll('#board ${sel(v)}')].map((el) => [el.dataset.id, { kept: el.__tag === el.dataset.id, text: el.textContent.replace(/\\s+/g, ' ').trim() }]))`);
const step = async () => { await ev(`await fetch('/__step', { method: 'PUT' }); await pollAgents(); true`); await sleep(50); };
const res = {};

for (const v of ['board', 'list']) {
  const r = res[v] = {};
  r.ready = await load(v);
  await ev(`await pollAgents(); true`);   // settle: nothing new
  // Open card a and type in the reply box, cursor in the middle.
  await ev(`openDetail('a'); showTab('conversation'); true`); await sleep(150);
  await ev(`$('#d-reply').value = ''; $('#d-reply').focus(); true`);   // the board view's draft comes back after the reload
  await send('Input.insertText', { text: 'hello world' });
  await ev(`$('#d-reply').setSelectionRange(5, 5); true`);
  await tagCards(v);
  await ev(`document.querySelectorAll('#d-log li').forEach((li, i) => li.__tag = 'log' + i); document.querySelectorAll('#d-log li.k-uses details ul li').forEach((li, i) => li.__step = i); true`);
  r.stepsBefore = await ev(`document.querySelectorAll('#d-log li.k-uses details ul li').length`);
  r.rendersBefore = await ev('window.__r');
  r.activeBefore = await ev(`document.activeElement && document.activeElement.id`);
  await ev(`window.__ids = []; { const f = refreshCards; refreshCards = function (ids) { window.__ids.push(ids); return f.apply(this, arguments); }; } true`);
  await step(); await step(); await step();
  r.refreshed = await ev(`typeof window.__ids === 'object' ? window.__ids : null`);
  r.renders = (await ev('window.__r')) - r.rendersBefore;
  r.cards = await cards(v);
  r.steps = await ev(`[...document.querySelectorAll('#d-log li.k-uses details ul li')].map((li) => ({ kept: li.__step !== undefined, text: li.textContent.replace(/\\s+/g, ' ').trim() }))`);
  r.logKept = await ev(`[...document.querySelectorAll('#d-log > li:not(.k-uses)')].map((li) => li.__tag !== undefined)`);
  r.usesKept = await ev(`document.querySelector('#d-log li.k-uses').__tag !== undefined`);
  r.reply = await ev(`({ value: $('#d-reply').value, start: $('#d-reply').selectionStart, end: $('#d-reply').selectionEnd, focused: document.activeElement === $('#d-reply') })`);
  // The session finishes: the card moves to Review, which needs the whole board redrawn.
  await ev(`document.activeElement.blur(); true`);
  const r0 = await ev('window.__r');
  await ev(`await fetch('/__finish', { method: 'PUT' }); await pollAgents(); true`); await sleep(400);
  r.finishRenders = (await ev('window.__r')) - r0;
  r.reviewCol = await ev(`task('a').column`);
  r.inReview = await ev(`(() => { const el = document.querySelector('#board ${sel(v)}[data-id="a"]'); return !!el && (${v === 'list' ? `el.closest('.lgrp').textContent.includes('Review')` : `el.closest('.column').dataset.col === 'rev'`}); })()`);
  await ev(`closeDetail(); true`);
  // Reset the stub for the next view.
  await ev(`await fetch('/__reset', { method: 'PUT' }); true`);
}

// Typing holds a full redraw back; it runs once the typing stops.
await load('board');
await ev(`openDetail('b'); true`); await sleep(100);
await ev(`$('#d-reply').focus(); true`);
await send('Input.insertText', { text: 'x' });
const t0 = await ev('window.__r');
await ev(`pollRender(); true`); await sleep(300);
res.typingRendersDuring = (await ev('window.__r')) - t0;
await sleep(1600);
res.typingRendersAfter = (await ev('window.__r')) - t0;
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
    orig_put = Stub.do_PUT

    def do_put(self):
        if self.path.split("?")[0] == "/__reset":
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            with lock:
                state.update({"steps": 2, "phase_a": "working", "hist": []})
                state["rev"] += 1   # a token from before is unknown now: the next poll takes every card
                for t in TASKS:
                    t["column"] = "prog"
            return self.send_json({"ok": True})
        return orig_put(self)
    Stub.do_PUT = Stub.do_POST = do_put

    port, dport = free_port(), free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    driver = os.path.join(TMP, "driver.mjs")
    open(driver, "w").write(DRIVER)
    try:
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome")],
                           capture_output=True, text=True, timeout=120)
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

    for v in ("board", "list"):
        r = res[v]
        case(f"{v}: setup: board drawn, reply box focused, 2 steps shown", r["ready"] and r["activeBefore"] == "d-reply" and r["stepsBefore"] == 2,
             (r["ready"], r["activeBefore"], r["stepsBefore"]))
        case(f"{v}: three step polls redraw no whole board", r["renders"] == 0, r["renders"])
        c = r["cards"]
        case(f"{v}: the other cards are the same elements", c.get("b", {}).get("kept") and c.get("c", {}).get("kept"), (c, r.get("refreshed")))
        case(f"{v}: the changed card was redrawn", "a" in c and not c["a"]["kept"], c.get("a"))
        if v == "board":
            case("board: the changed card shows its newest step", "step 4" in c.get("a", {}).get("text", ""), c.get("a"))
        s = r["steps"]
        case(f"{v}: step list grew to 5 rows", len(s) == 5 and s[-1]["text"].endswith("step 4"), s)
        case(f"{v}: the old step rows were kept, the new ones added", [x["kept"] for x in s] == [True, True, False, False, False], s)
        case(f"{v}: the conversation's messages were kept", r["logKept"] and all(r["logKept"]) and r["usesKept"], (r["logKept"], r["usesKept"]))
        rp = r["reply"]
        case(f"{v}: reply box keeps its text, focus and cursor", rp == {"value": "hello world", "start": 5, "end": 5, "focused": True}, rp)
        case(f"{v}: a finished session moves the card to Review with a full redraw", r["reviewCol"] == "rev" and r["finishRenders"] >= 1 and r["inReview"],
             (r["reviewCol"], r["finishRenders"], r["inReview"]))
    case("typing: a full redraw waits while someone types", res["typingRendersDuring"] == 0, res["typingRendersDuring"])
    case("typing: it runs once the typing stops", res["typingRendersAfter"] >= 1, res["typingRendersAfter"])
    case("no uncaught page exceptions", res["errors"] == [], res["errors"])

    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    print("VERDICT:", "FAIL" if fails else "PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
