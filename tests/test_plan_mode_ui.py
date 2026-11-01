"""A plan card on the real page: the "Plan first" tick, the live pane beside the chat that grows without a reload, the
Plan tab that reads the documents, and "Build this plan". Drives the real index.html + i18n.js in headless Chrome over
raw CDP, with /api faked by a stub server on a free port (never the live board). Plan data goes through the real
reports.py, into a temp board.

Run: python3 tests/test_plan_mode_ui.py [screenshot folder]  (stdlib + node 22+ + Google Chrome)."""
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
sys.path.insert(0, ROOT)
import reports  # noqa: E402

TMP = tempfile.mkdtemp(prefix="plan-mode-ui-")
BOARD_DIR = os.path.join(TMP, ".task_board")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else TMP
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}


def task(tid, title, col="c2", agent=True, **more):
    return {"id": tid, "title": title, "notes": "", "priority": "", "tags": ["ui"], "column": col, "parent": None, "order": 0,
            "created": "2026-11-01T11:00:00+00:00", "updated": "2099-01-01T00:00:00+00:00",
            **({"agent": {"id": tid, "started": "2026-11-01T11:47:00+00:00"}} if agent else {}), **more}


BOARD = {"version": 1, "columns": [{"id": "c1", "name": "To Do", "done": False},
                                   {"id": "c2", "name": "In Progress", "done": False, "role": "progress"},
                                   {"id": "c3", "name": "Review", "done": False, "role": "review"},
                                   {"id": "c4", "name": "Done", "done": True}],
         "tasks": [task("p1", "Add dark mode", planMode=True, answerOnly=True), task("n1", "A plain card"),
                   task("d1", "An idea", col="c1", agent=False)]}
SAVED = {"tasks": None}
PHASE = {"p1": "working"}

reports.update_plan(BOARD_DIR, "p1", {
    "goal": "Let people switch the board to dark colours", "phase": "explore",
    "nodes": [{"id": "g", "kind": "goal", "label": "Dark mode on every page", "status": "working"},
              {"id": "q", "parent": "g", "kind": "decision", "label": "Where does the switch live?", "status": "decided"},
              {"id": "q1", "parent": "q", "kind": "option", "label": "In the menu", "status": "decided", "detail": "It stays out of the way"},
              {"id": "q2", "parent": "q", "kind": "option", "label": "On every card", "status": "dropped"},
              {"id": "x", "parent": "g", "kind": "risk", "label": "<img src=x onerror=window.cpXss=1> breaks print", "status": "open"}],
    "files": [{"path": "index.html", "do": "change", "why": "the colours live here"},
              {"path": "i18n.js", "do": "look", "why": "the words"}]})
reports.append(BOARD_DIR, "p1", "question", "Where should the switch live?", "claude", options=["In the menu", "On every card"])
reports.append(BOARD_DIR, "n1", "progress", "Plain work.", "claude")


def view(tid):
    plan = reports.read_plan(BOARD_DIR, tid)
    out = {**plan, "docs": reports.plan_docs(BOARD_DIR, tid), "dir": reports.plan_dir(BOARD_DIR, tid),
           "lookups": [{"tool": "map", "what": "search_graph: dark colours", "at": 1}, {"tool": "read", "what": "/x/index.html", "at": 1}]}
    sig = str(hash(json.dumps(out, sort_keys=True)))
    return {**out, "sig": sig}


def agents():
    return {tid: {"id": tid, "state": "working", "phase": PHASE.get(tid, "finished"), "reason": "", "log": reports.read(BOARD_DIR, tid)}
            for tid in ("p1", "n1")}


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
            return self.send_json([{"id": "demo", "name": "demo", "path": TMP, "board": BOARD_DIR}])
        if p == "/api/tasks":
            return self.send_json(SAVED["tasks"] or BOARD)
        if p == "/api/agents":
            return self.send_json(agents())
        if p == "/api/plan":
            tid = self.path.split("&t=")[1].split("&")[0]
            if tid != "p1":
                return self.send_json({"error": "not a plan card"}, 404)
            return self.send_json(view(tid))
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
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.startswith("/api/tasks"):
            SAVED["tasks"] = json.loads(body)
        folder = reports.plan_dir(BOARD_DIR, "p1")
        if self.path == "/__test/grow":
            reports.update_plan(BOARD_DIR, "p1", {"phase": "draft", "nodes": [{"id": "new", "parent": "g", "kind": "task", "label": "Add the switch"}],
                                                  "files": [{"path": "server.py", "do": "add", "why": "saves the choice"}]})
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "brief.md"), "w") as f:
                f.write("# The brief\n\n- **Goal**: dark colours\n<script>window.cpXss=2</script>\n")
        if self.path == "/__test/ready":
            PHASE["p1"] = "finished"
            reports.update_plan(BOARD_DIR, "p1", {"phase": "ready"})
            with open(os.path.join(folder, "plan.md"), "w") as f:
                f.write("## Steps\n\n1. Add the switch in the menu\n")
            reports.append(BOARD_DIR, "p1", "done", "The plan is ready.", "claude")
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
const [url, chrome, dport, profile, shots] = process.argv.slice(2);
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
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
  if (m.method === 'Runtime.exceptionThrown') errors.push('exception: ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push('console.error: ' + m.params.args.map(a => a.value ?? a.description).join(' '));
});
const send = (method, params = {}) => new Promise(r => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, replMode: true }); return r.result.exceptionDetails ? { exc: r.result.exceptionDetails.exception?.description } : r.result.result.value; };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const size = (width, height) => send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
const shot = async (name) => { const { result } = await send('Page.captureScreenshot', { format: 'png' }); (await import('node:fs')).writeFileSync(`${shots}/${name}.png`, Buffer.from(result.data, 'base64')); };
await send('Page.enable'); await send('Runtime.enable');
await size(1500, 950);
await send('Page.navigate', { url });
for (let i = 0; i < 100; i++) { await sleep(100); if (await ev(`typeof polledOnce !== 'undefined' && polledOnce && !!task('p1')`) === true) break; }
const out = {};
const LAYOUT = `(() => { const r = (s) => document.querySelector(s).getBoundingClientRect(); const a = r('#d-cplan'), b = r('#d-log');
  return { beside: a.left >= b.right - 1, above: a.bottom <= b.top + 1, pane: !document.querySelector('#d-cplan').hidden, paneW: a.width }; })()`;

// The tick on a card not sent yet.
await ev(`openDetail('d1')`); await sleep(100);
out.draft = await ev(`({ shown: !!document.querySelector('#d-planmode').offsetParent, tab: document.querySelector('#d-plan-tab').hidden })`);
await ev(`document.querySelector('#d-planmode').click()`); await sleep(50);
out.ticked = await ev(`({ plan: task('d1').planMode, answer: task('d1').answerOnly, ansBox: document.querySelector('#d-answeronly').checked, ansLocked: document.querySelector('#d-answeronly').disabled, wt: document.querySelector('#d-worktree').checked })`);
await ev(`document.querySelector('#d-planmode').click()`); await sleep(50);
out.unticked = await ev(`({ plan: task('d1').planMode, answer: task('d1').answerOnly, ansLocked: document.querySelector('#d-answeronly').disabled, wt: document.querySelector('#d-worktree').checked })`);
await ev(`closeDetail()`);

// A plain card shows none of it.
await ev(`openDetail('n1'); showTab('conversation')`); await sleep(200);
out.plain = await ev(`({ tab: document.querySelector('#d-plan-tab').hidden, pane: document.querySelector('#d-cplan').hidden, withPlan: document.querySelector('#d-tab-conversation').classList.contains('with-plan') })`);
await ev(`closeDetail()`);

// The plan card, live: the pane sits beside the chat.
await ev(`openDetail('p1'); showTab('conversation')`); await sleep(600);
out.wide = await ev(LAYOUT);
out.first = await ev(`({ tab: !document.querySelector('#d-plan-tab').hidden, text: document.querySelector('#d-cplan').innerText, step: document.querySelector('.cp-step.now')?.innerText,
  nodes: document.querySelectorAll('.cp-node').length, flash: document.querySelectorAll('.cp-node.fresh').length,
  nest: document.querySelector('.cp-node.k-option')?.parentElement.parentElement.classList.contains('k-decision'), dropped: !!document.querySelector('.cp-node.s-dropped'),
  xss: window.cpXss || 0, imgs: document.querySelectorAll('#d-cplan img').length, next: document.querySelector('#d-next-title').innerText })`);
await shot('plan-pane-beside-chat');
await ev(`document.querySelector('#d-cplan').scrollTop = 40`);

// It grows with no reload.
await ev(`fetch('/__test/grow', { method: 'PUT' })`);
let grown = false, flashed = false;
for (let i = 0; i < 40 && !grown; i++) { await sleep(100); grown = await ev(`!!document.querySelector('.cp-node.fresh')`) === true; flashed = flashed || grown; }
out.grown = await ev(`({ text: document.querySelector('#d-cplan').innerText, step: document.querySelector('.cp-step.now')?.innerText, past: document.querySelectorAll('.cp-step.past').length,
  fresh: [...document.querySelectorAll('.cp-node.fresh .cp-label')].map(e => e.innerText), docs: [...document.querySelectorAll('.cp-doc')].map(e => e.innerText), top: document.querySelector('#d-cplan').scrollTop })`);
out.flashed = flashed;

// A document button leads to the Plan tab, which reads it.
await ev(`document.querySelector('.cp-doc').click()`); await sleep(200);
out.doc = await ev(`({ tab: mainTab, moved: document.querySelector('#d-cplan').parentElement.id, body: document.querySelector('#d-cplan-doc').innerText, h: !!document.querySelector('#d-cplan-doc b, #d-cplan-doc strong'), xss: window.cpXss || 0,
  on: document.querySelector('.cp-doc.on')?.innerText, composer: document.querySelector('#d-reply-wrap').classList.contains('on'), docVisible: !!document.querySelector('#d-cplan-doc').offsetParent })`);
await shot('plan-tab-document');
await ev(`showTab('summary')`); await sleep(100);
out.summary = await ev(`document.querySelector('#d-cplan').hidden`);
await ev(`showTab('conversation')`); await sleep(100);

// Narrow: above the chat, not squeezed beside it.
await size(760, 900); await sleep(300);
out.narrow = await ev(LAYOUT);
await shot('plan-pane-narrow');
await size(1500, 950); await sleep(300);

// The plan is finished: the card offers to build it, from the document itself.
await ev(`fetch('/__test/ready', { method: 'PUT' })`);
for (let i = 0; i < 80; i++) { await sleep(100); if (await ev(`document.querySelector('#d-next-title').innerText.includes('ready')`) === true) break; }
out.ready = await ev(`({ title: document.querySelector('#d-next-title').innerText, buttons: [...document.querySelectorAll('#d-next-actions button')].map(b => b.innerText) })`);
await shot('plan-ready');
await ev(`[...document.querySelectorAll('#d-next-actions button')].find(b => b.innerText.includes('Build this plan')).click()`); await sleep(300);
out.built = await ev(`(() => { const t = task(openId); return { other: t.id !== 'p1', title: t.title, notes: t.notes, tags: t.tags, plan: !!t.planMode, answer: !!t.answerOnly, sent: !!t.agent, tab: document.querySelector('#d-plan-tab').hidden }; })()`);
await ev(`closeDetail()`);
out.errors = errors;
console.log(JSON.stringify(out));
quit(0);
"""


srv = ThreadingHTTPServer(("127.0.0.1", free_port()), Stub)
threading.Thread(target=srv.serve_forever, daemon=True).start()
driver = os.path.join(TMP, "driver.mjs")
with open(driver, "w") as f:
    f.write(DRIVER)
os.makedirs(SHOTS, exist_ok=True)
r = subprocess.run([NODE, driver, f"http://127.0.0.1:{srv.server_port}/", CHROME, str(free_port()), os.path.join(TMP, "profile"), SHOTS],
                   capture_output=True, text=True, timeout=90)
try:
    o = json.loads(r.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    sys.exit("driver gave nothing:\n" + r.stdout + r.stderr)

fails = 0


def check(name, ok, extra=None):
    global fails
    print(("ok  " if ok else "FAIL") + " " + name + (f" -> {extra}" if not ok else ""))
    fails += 0 if ok else 1


check("no page errors", not o.get("errors"), o.get("errors"))
check("the tick shows on a new card, no Plan tab yet", o["draft"] == {"shown": True, "tab": True}, o["draft"])
check("ticking turns on the safe mode and locks it", o["ticked"] == {"plan": True, "answer": True, "ansBox": True, "ansLocked": True, "wt": False}, o["ticked"])
check("unticking gives it all back", o["unticked"] == {"plan": False, "answer": False, "ansLocked": False, "wt": True}, o["unticked"])
check("a plain card has no pane and no tab", o["plain"] == {"tab": True, "pane": True, "withPlan": False}, o["plain"])
check("the pane sits beside the chat", o["wide"]["pane"] and o["wide"]["beside"], o["wide"])
check("the pane has room", o["wide"]["paneW"] >= 280, o["wide"])
f = o["first"]
check("a plan card has the Plan tab", f["tab"])
check("the phase shows", f["step"] == "Explore", f["step"])
for word in ("Let people switch", "Where does the switch live?", "It stays out of the way", "index.html", "the colours live here", "i18n.js",
             "search_graph: dark colours", "FILES TO CHANGE", "FILES TO LOOK AT", "JUST LOOKED UP"):
    check(f"the pane shows {word}", word in f["text"], f["text"])
check("every node is drawn", f["nodes"] == 5, f["nodes"])
check("an option hangs under its decision", f["nest"] is True)
check("a dropped option is marked", f["dropped"] is True)
check("the first draw does not flash", f["flash"] == 0, f["flash"])
check("a label cannot run code", f["xss"] == 0 and f["imgs"] == 0, f)
check("a working session is not called unfinished", "Work in progress" in f["next"], f["next"])
g = o["grown"]
check("the tree grows with no reload", "Add the switch" in g["text"], g["text"])
check("the new node flashes", o["flashed"] and "Add the switch" in g["fresh"], g["fresh"])
check("only the new node flashes", g["fresh"] == ["Add the switch"], g["fresh"])
check("the phase moves on", g["step"] == "Draft" and g["past"] == 1, g)
check("a new file shows", "server.py" in g["text"] and "FILES TO ADD" in g["text"], g["text"])
check("a new document shows", g["docs"] == ["brief.md"], g["docs"])
d = o["doc"]
check("a document button opens the Plan tab", d["tab"] == "plan" and d["moved"] == "d-tab-plan", d)
check("the document is rendered", "The brief" in d["body"] and d["h"], d)
check("a document cannot run code", d["xss"] == 0, d)
check("the open document is marked", d["on"] == "brief.md", d)
check("you can still reply from the Plan tab", d["composer"] is True, d)
check("the pane stays out of Summary", o["summary"] is True)
check("narrow: the pane is above the chat", o["narrow"]["above"] and not o["narrow"]["beside"], o["narrow"])
r_ = o["ready"]
check("a finished plan says so", "ready" in r_["title"], r_)
check("it offers Build this plan first", r_["buttons"][:1] == ["Build this plan"], r_)
b = o["built"]
check("Build this plan makes a new card and opens it", b["other"] and not b["sent"], b)
check("the new card is named for the plan", b["title"] == "Build: Add dark mode", b["title"])
check("the new card holds the whole plan", "Add the switch in the menu" in b["notes"] and "p1" in b["notes"], b["notes"])
check("the new card may change things", not b["plan"] and not b["answer"], b)
check("the new card keeps the tags", b["tags"] == ["ui"], b["tags"])
print(f"\n{'FAILED: ' + str(fails) if fails else 'all ok'}; shots in {SHOTS}")
sys.exit(1 if fails else 0)
