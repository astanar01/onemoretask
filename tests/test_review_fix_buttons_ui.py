"""A code review's findings get one button per finding plus "Fix everything", in Conversation and in Summary.

You pick several (Fix everything picks all, a second click none), then Send: one reply naming the picks (plus any
typed reply box text) goes to the reviewer the card follows, or through /api/agent/handback when the review was by
the other agent. Picks show in both places. Once you answered, the buttons stay, disabled, with your picks marked;
a later fix report gets none, and neither does a bare "Finding 1 (major): fixed" status line from a second review.
Drives the real index.html + i18n.js in headless Chrome over raw CDP, with /api faked by a stub server on a free port
(never the live board, never .task_board).

Run: python3 tests/test_review_fix_buttons_ui.py  (stdlib + node 22+ + Google Chrome).
INDEX=<file> serves another index.html (e.g. the previous commit's, to see the test fail on the old page).
SHOT=<file.png> saves a screenshot of the Summary tab with the buttons."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="review-fix-buttons-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
INDEX = os.environ.get("INDEX") or os.path.join(ROOT, "index.html")
SHOT = os.environ.get("SHOT", "")
SERVED = {"/": INDEX, "/index.html": INDEX, "/i18n.js": os.path.join(ROOT, "i18n.js")}
OLD = "2026-11-11T09:00:00+00:00"

FINDINGS = """Finding 1 (major): a kind that ends with a backslash breaks the Kind menu

- Where: `app/Services/TraitService.php:334`
- Problem: kinds after it can no longer be picked.
- Fix: use a plain list lookup.
- Proof: Reproduced.

Finding 2 (minor): the x on a kind only hides it

- Where: `resources/js/kinds.js:12`
- Problem: it is saved only on Save.
- Fix: say so in the tooltip.
- Proof: Not reproduced — confidence medium.

Finding 3 (minor): the empty list shows no hint

- Where: `resources/js/kinds.js:30`
- Fix: show "no kinds yet".

Checked and fine: the 32-letter limit.

Which findings should I fix?"""

# A second review: finding 1 of the first one is only a status line now; finding 2 is new.
SECOND = """Finding 1 (major): fixed

Finding 2 (minor): the menu loses its order

- Where: `resources/js/kinds.js:40`
- Fix: sort by name.

Which findings should I fix?"""


def entry(frm, status, msg):
    return {"from": frm, "status": status, "at": OLD, "message": msg}


BASE = [entry("claude", "done", "Kinds are a list now. Commit abc1234."),
        entry("you", "review", "Code review started (task model, session r1)")]
COLS = [{"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "rev", "name": "Review", "done": False, "role": "review"},
        {"id": "done", "name": "Done", "done": True}]


def card(cid):
    return {"id": cid, "title": "Card " + cid, "notes": "", "priority": "", "tags": [], "column": "rev", "parent": None,
            "order": 0, "created": OLD, "updated": OLD, "agent": {"id": "a-" + cid, "started": OLD}}


def agent(cid, log, **kw):
    return {"id": "a-" + cid, "sessionId": "s-" + cid, "state": "idle", "phase": "needs_you", "reason": "", "log": log,
            "mcp_uses": [], "mcp": None, **kw}


IDS = ["c1", "c2", "c3", "c4"]
AGENTS = {
    "c1": agent("c1", BASE + [entry("claude", "done", FINDINGS)]),
    "c2": agent("c2", BASE + [entry("claude", "done", FINDINGS)], handback="Claude"),
    "c3": agent("c3", BASE + [entry("claude", "done", FINDINGS), entry("you", "reply", "Fix findings 1, 3\n\nKeep it small"),
                              entry("claude", "done", "Finding 1 (major): fixed\n\n- Commit: def5678\n- Proof: test passes")]),
    "c4": agent("c4", BASE + [entry("claude", "done", FINDINGS), entry("you", "reply", "Fix everything"),
                              entry("claude", "done", "All fixed."),
                              entry("you", "review", "Code review started (task model, session r2)"),
                              entry("claude", "done", SECOND)]),
}
BOARD = {"version": 1, "columns": COLS, "tasks": [card(c) for c in IDS]}
POSTS = []
LOCK = threading.Lock()


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
            with LOCK:
                return self.send_json(AGENTS)
        if p == "/api/app-copy":
            return self.send_json({"current": ROOT, "copies": [{"path": ROOT, "main": True}]})
        if p.startswith("/api/"):
            return self.send_json({})
        if p in SERVED:
            b = open(SERVED[p], "rb").read()
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

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        path = self.path.split("?")[0]
        if path not in ("/api/agent/reply", "/api/agent/handback"):
            return self.send_json({"ok": True})
        tid, text = body.get("taskId"), (body.get("text") or "").strip()
        # Logged the way server.py does: a reply as is, a handback behind reports.HANDBACK.
        msg = text if path.endswith("reply") else "Sent the review findings back to Claude: " + text
        with LOCK:
            POSTS.append([path, tid, text])
            AGENTS[tid]["log"] = AGENTS[tid]["log"] + [
                {"from": "you", "status": "reply", "at": datetime.now(timezone.utc).isoformat(), "message": msg}]
            AGENTS[tid]["phase"] = "working"
        return self.send_json({"ok": True, "id": "x", "message": msg} if path.endswith("reply") else {"ok": True, "id": "x"})


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
setTimeout(() => { console.log(JSON.stringify({ error: 'HUNG driver' })); quit(2); }, 80000);
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
// Each button: [label, disabled, picked], in Conversation and in Summary; send: [disabled, count line] or null.
const look = `({ conv: [...document.querySelectorAll('#d-log button[data-fix]')].map((b) => [b.textContent, b.disabled, b.classList.contains('picked')]),
  sum: [...document.querySelectorAll('#d-sum-verify button[data-fix]')].map((b) => [b.textContent, b.disabled, b.classList.contains('picked')]),
  send: [...document.querySelectorAll('#d-log [data-fixsend], #d-sum-verify [data-fixsend]')].map((b) => [b.disabled, b.nextElementSibling.textContent]),
  tips: [...document.querySelectorAll('#d-log button[data-fix]')].map((b) => b.title) })`;
const click = async (sel, k = 0) => { await ev(`document.querySelectorAll(${JSON.stringify(sel)})[${k}].click()`); await sleep(50); };
const open = async (c) => { await ev(`closeDetail && closeDetail()`); await ev(`openDetail('${c}'); renderSummary(task('${c}'))`); await sleep(150); };
for (const c of ['c3', 'c4']) { await open(c); res[c] = await ev(look); }
await open('c1');
res.c1 = await ev(look);
// Fix everything picks all, a second click none.
await ev(`showTab('summary')`);
await click('#d-sum-verify button[data-fix]', 0); res.c1all = await ev(look);
await click('#d-sum-verify button[data-fix]', 0); res.c1none = await ev(look);
// Pick 1 and 2 (not 3): in Summary for 1, in Conversation for 2; both places show both.
await click('#d-sum-verify button[data-fix]', 1); await click('#d-log button[data-fix]', 2);
res.c1picked = await ev(look);
await ev(`renderAgent(task('c1')); renderSummary(task('c1'))`); res.c1redraw = await ev(look);
if (shot) { await ev(`showTab('summary')`); await sleep(200);
  await ev(`$('#d-sum-verify .fixopts').scrollIntoView({ block: 'center' })`); await sleep(150);
  const r = await ev(`(() => { const f = [...document.querySelectorAll('#d-sum-verify .finding')].pop().getBoundingClientRect(), b = $('#d-sum-verify .fixopts').getBoundingClientRect(); return { x: f.x, y: f.y, w: f.width, h: b.bottom - f.y }; })()`);
  const s = await send('Page.captureScreenshot', { format: 'png', clip: { x: Math.max(0, r.x - 12), y: Math.max(0, r.y - 12), width: r.w + 24, height: r.h + 24, scale: 1 } });
  writeFileSync(shot, Buffer.from(s.result.data, 'base64')); }
// Send from the Summary box, with words typed in the reply box: one reply to the reviewer.
await ev(`showTab('summary')`);
await ev(`$('#d-reply').value = 'Keep it small'`);
await click('#d-sum-verify [data-fixsend]');
for (let i = 0; i < 3; i++) { await sleep(200); await ev(`await pollAgents()`); }
res.c1after = await ev(look);
res.c1tab = await ev(`mainTab`);
// The other agent reviewed: "Fix everything" goes back to the worker through the handback.
await open('c2');
res.c2 = await ev(look);
await click('#d-log button[data-fix]', 0); await click('#d-log [data-fixsend]');
for (let i = 0; i < 3; i++) { await sleep(200); await ev(`await pollAgents()`); }
res.c2after = await ev(look);
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
                           capture_output=True, text=True, timeout=120)
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
    B = lambda l, d=False, p=False: [l, d, p]
    four = [B("Fix everything"), B("Fix finding 1"), B("Fix finding 2"), B("Fix finding 3")]
    check("page loaded and polled", res["ready"])
    check("no page errors", res["errors"], [])
    check("review findings: buttons in Conversation", res["c1"]["conv"], four)
    check("review findings: same buttons in Summary", res["c1"]["sum"], four)
    check("nothing picked yet: Send is off in both places", res["c1"]["send"], [[True, "0 of 3 picked"]] * 2)
    check("each finding button's tooltip is its title", res["c1"]["tips"][1:],
          ["a kind that ends with a backslash breaks the Kind menu", "the x on a kind only hides it", "the empty list shows no hint"])
    check("Fix everything picks all", res["c1all"]["sum"], [B(x[0], False, True) for x in four])
    check("Fix everything again picks none", (res["c1none"]["sum"], res["c1none"]["send"]), (four, [[True, "0 of 3 picked"]] * 2))
    two = [B("Fix everything"), B("Fix finding 1", False, True), B("Fix finding 2", False, True), B("Fix finding 3")]
    check("picks 1 and 2 show in both places, Send on", (res["c1picked"]["conv"], res["c1picked"]["sum"], res["c1picked"]["send"]),
          (two, two, [[False, "2 of 3 picked"]] * 2))
    check("picks survive a redraw (a poll)", res["c1redraw"]["sum"], two)
    check("Send sent one reply naming 1 and 2, plus the typed words", [x for x in POSTS if x[1] == "c1"],
          [["/api/agent/reply", "c1", "Fix findings 1, 2\n\nKeep it small"]])
    done2 = [B("Fix everything", True), B("✓ Fix finding 1", True, True), B("✓ Fix finding 2", True, True), B("Fix finding 3", True)]
    check("after Send: Conversation tab, picks marked, all disabled, no Send", (res["c1tab"], res["c1after"]["conv"], res["c1after"]["send"]),
          ("conversation", done2, []))
    check("after Send: Summary shows the same", res["c1after"]["sum"], done2)
    check("other agent's review: same buttons", res["c2"]["conv"], four)
    check("other agent's review: Fix everything goes back to the worker", [x for x in POSTS if x[1] == "c2"],
          [["/api/agent/handback", "c2", "Fix everything"]])
    check("other agent's review: all marked after the handback", res["c2after"]["conv"],
          [B("✓ " + x[0], True, True) for x in four])
    check("answered review + fix report: only the old, disabled buttons with the picks; none on the fix report or in Summary",
          (res["c3"]["conv"], res["c3"]["sum"]),
          ([B("Fix everything", True), B("✓ Fix finding 1", True, True), B("Fix finding 2", True), B("✓ Fix finding 3", True, True)], []))
    check("second review: only its report gets buttons; a bare status line gets none; one finding, no Fix everything",
          (res["c4"]["conv"], res["c4"]["sum"]), ([B("Fix finding 2")], [B("Fix finding 2")]))
    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
