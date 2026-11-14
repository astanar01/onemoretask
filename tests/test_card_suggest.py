"""A card Claude suggests (report.py card) is stored as a progress entry with a checked `card`, bad input is refused,
and the Conversation tab shows it with an Add it button that adds the card to the first open column once, keeps
its notes and tags, and then shows Added + Open it (also after a reload).
Drives the real index.html + i18n.js in headless Chrome over raw CDP, with /api faked by a stub server on a free
port (never the live board, never .task_board).

Run: python3 tests/test_card_suggest.py  (stdlib + node 22+ + Google Chrome). SHOT=<png> saves a screenshot."""
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

TMP = tempfile.mkdtemp(prefix="card-suggest-test-")
BOARD_DIR = os.path.join(TMP, ".task_board")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}
GOOD = {"title": "  Let   Claude make changes in a world ", "notes": "**Goal:** add and change pages.\n\n- step one",
        "tags": ["feature", " ", "feature", "world"]}

fails = []


def check(name, cond, got=None):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond or got is None else f"  (got {got!r})"))
    if not cond:
        fails.append(name)


def cli(*args, payload=None):
    return subprocess.run([sys.executable, os.path.join(ROOT, "report.py"), "--board", BOARD_DIR, "t1", *args],
                          input=payload, capture_output=True, text=True)


# --- report.py ---
r = cli("card", "-", payload=json.dumps(GOOD))
check("cli accepts a card", r.returncode == 0 and "reported card" in r.stdout, r.stdout + r.stderr)
e = reports.read(BOARD_DIR, "t1")[-1]
check("stored as progress from claude", e.get("status") == "progress" and e.get("from") == "claude")
check("message names the card", e.get("message") == "Suggested card: Let Claude make changes in a world", e.get("message"))
check("card cleaned", e.get("card") == {"title": "Let Claude make changes in a world", "notes": GOOD["notes"],
                                         "tags": ["feature", "world"]}, e.get("card"))
fpath = os.path.join(TMP, "card.json")
with open(fpath, "w") as f:
    json.dump({"title": "From a file"}, f)
r = cli("card", "--file", fpath)
check("--file form works", r.returncode == 0 and reports.read(BOARD_DIR, "t1")[-1]["card"]["title"] == "From a file",
      r.stderr)
for name, bad in [("no title", {"notes": "x"}), ("blank title", {"title": "  "}), ("title too long", {"title": "x" * 201}),
                  ("tags not a list", {"title": "a", "tags": "x"}), ("too many tags", {"title": "a", "tags": list("abcdefghi")}),
                  ("not an object", ["a"])]:
    r = cli("card", json.dumps(bad))
    check(f"refused: {name}", r.returncode != 0 and "card:" in r.stderr, r.stderr)
r = cli("card", "not json")
check("refused: not JSON", r.returncode != 0 and "card:" in r.stderr, r.stderr)

# --- the page ---
LOG = [x for x in reports.read(BOARD_DIR, "t1") if x.get("card")][:1]
LOG.insert(0, {"at": "2026-11-14T10:00:00+00:00", "status": "progress", "message": "Here is the card.", "from": "claude"})
LOG.append({"at": "2026-11-14T10:05:00+00:00", "status": "done", "message": "Done.", "from": "claude"})


def task(tid, title, col):
    return {"id": tid, "title": title, "notes": "", "priority": "", "tags": [], "column": col, "parent": None, "order": 0,
            "created": "2026-11-14T09:00:00+00:00", "updated": "2099-01-01T00:00:00+00:00",
            "agent": {"id": tid, "started": "2026-11-14T09:01:00+00:00"}}


BOARD = {"version": 1, "columns": [{"id": "c1", "name": "To Do", "done": False},
                                   {"id": "c2", "name": "In Progress", "done": False, "role": "progress"},
                                   {"id": "c3", "name": "Done", "done": True}],
         "tasks": [task("t1", "Asks for a card", "c2")]}
PUTS = []


class Stub(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Board-Rev", str(len(PUTS)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/projects":
            return self.send_json([{"id": "demo", "name": "demo", "path": TMP, "board": BOARD_DIR}])
        if p == "/api/tasks":
            return self.send_json(BOARD)
        if p == "/api/agents":
            return self.send_json({"t1": {"id": "t1", "state": "idle", "phase": "done", "reason": "", "log": LOG}})
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
        if self.path.split("?")[0] == "/api/tasks":
            data = json.loads(body)
            BOARD["columns"], BOARD["tasks"] = data["columns"], data["tasks"]
            PUTS.append(data)
            self.send_response(204)
            self.send_header("X-Board-Rev", str(len(PUTS)))
            self.end_headers()
            return
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
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
  if (m.method === 'Runtime.exceptionThrown') errors.push('exception: ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push('console.error: ' + m.params.args.map(a => a.value ?? a.description).join(' '));
});
const send = (method, params = {}) => new Promise(r => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, replMode: true }); return r.result.exceptionDetails ? { exc: r.result.exceptionDetails.exception?.description } : r.result.result.value; };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1000, height: 900, deviceScaleFactor: 1, mobile: false });
const load = async () => {
  await send('Page.navigate', { url });
  for (let i = 0; i < 100; i++) { await sleep(100); if (await ev(`typeof polledOnce !== 'undefined' && polledOnce && !!task('t1')`) === true) return true; }
  return false;
};
const out = { ready: await load() };
if (!out.ready) { out.errors = errors; console.log(JSON.stringify(out)); quit(0); }
const LOOK = `(() => { const b = document.querySelector('#d-log .sugcard');
  return b ? { title: b.querySelector('.sc-title')?.textContent, head: b.querySelector('.sc-head')?.textContent,
    notesBold: !!b.querySelector('.sc-notes strong, .sc-notes b'), tags: [...b.querySelectorAll('.sc-tags .tag')].map(x => x.textContent),
    add: !!b.querySelector('[data-card-add]'), done: b.querySelector('.sc-done')?.textContent || null, open: b.querySelector('[data-card-open]')?.dataset.cardOpen || null,
    rawMsg: b.closest('li').querySelector('.msg') ? b.closest('li').querySelector('.msg').textContent : null } : null; })()`;
await ev(`openDetail('t1'); showTab('conversation'); true`);
await sleep(400);
out.before = await ev(LOOK);
out.nTasks0 = await ev(`state.tasks.length`);
if (shot) { const s = await send('Page.captureScreenshot', { format: 'png' }); writeFileSync(shot, Buffer.from(s.result.data, 'base64')); }
// Two quick clicks: only one card.
await ev(`(() => { const b = document.querySelector('[data-card-add]'); b.click(); b.click(); return true; })()`);
await sleep(300);
out.after = await ev(LOOK);
if (shot) { const s = await send('Page.captureScreenshot', { format: 'png' }); writeFileSync(shot.replace(/\.png$/, '-added.png'), Buffer.from(s.result.data, 'base64')); }
out.added = await ev(`state.tasks.filter((x) => x.fromCard).map((x) => ({ title: x.title, notes: x.notes, tags: x.tags, column: x.column, fromCard: x.fromCard }))`);
out.firstOpen = await ev(`firstOpenCol()`);
out.topOfCol = await ev(`(() => { const c = state.tasks.find((x) => x.fromCard); return state.tasks.filter((x) => x.column === c.column && !x.parent).every((x) => x === c || x.order >= c.order); })()`);
await sleep(1500);   // the page's save goes out
out.reload = await load();
await ev(`openDetail('t1'); showTab('conversation'); true`);
await sleep(400);
out.afterReload = await ev(LOOK);
out.nTasks1 = await ev(`state.tasks.length`);
await ev(`document.querySelector('[data-card-open]').click(); true`);
await sleep(300);
out.openedId = await ev(`openId`);
out.openedTitle = await ev(`document.querySelector('#d-title').value`);
out.errors = errors;
console.log(JSON.stringify(out));
quit(0);
"""

port = free_port()
srv = ThreadingHTTPServer(("127.0.0.1", port), Stub)
threading.Thread(target=srv.serve_forever, daemon=True).start()
drv = os.path.join(TMP, "driver.mjs")
with open(drv, "w") as f:
    f.write(DRIVER)
res = subprocess.run([NODE, drv, f"http://127.0.0.1:{port}/", CHROME, str(free_port()), os.path.join(TMP, "profile"),
                      os.environ.get("SHOT", "")], capture_output=True, text=True, timeout=90)
srv.shutdown()
try:
    out = json.loads(res.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    out = {}
    print(res.stdout, res.stderr)
check("page ready", out.get("ready") is True, out)
b = out.get("before") or {}
check("box shows the title", b.get("title") == "Let Claude make changes in a world", b)
check("box has its heading", b.get("head") == "Suggested card", b)
check("notes rendered as markdown", b.get("notesBold") is True, b)
check("tags shown", b.get("tags") == ["feature", "world"], b)
check("Add it button before adding", b.get("add") is True and not b.get("done"), b)
check("plain message line not repeated", b.get("rawMsg") is None, b)
added = out.get("added") or []
check("one card added for two clicks", len(added) == 1 and out.get("nTasks0", 0) + 1 == len(BOARD["tasks"]), added)
a = added[0] if added else {}
check("new card keeps title, notes, tags", a.get("title") == "Let Claude make changes in a world"
      and a.get("notes") == GOOD["notes"] and a.get("tags") == ["feature", "world"], a)
check("new card in the first open column, on top", a.get("column") == out.get("firstOpen") == "c1" and out.get("topOfCol") is True, a)
check("fromCard names the task and entry", a.get("fromCard") == "t1@" + LOG[1]["at"], a)
af = out.get("after") or {}
check("button turns into Added + Open it", af.get("add") is False and (af.get("done") or "").startswith("✓") and af.get("open"), af)
check("saved to the board file", any(any(t.get("fromCard") for t in p["tasks"]) for p in PUTS))
ar = out.get("afterReload") or {}
check("still Added after a reload, no second card", out.get("reload") and ar.get("add") is False and ar.get("open")
      and out.get("nTasks1") == out.get("nTasks0", 0) + 1, (ar, out.get("nTasks1")))
check("Open it opens the new card", out.get("openedId") == ar.get("open") and out.get("openedTitle") == "Let Claude make changes in a world",
      (out.get("openedId"), out.get("openedTitle")))
check("no page errors", not out.get("errors"), out.get("errors"))
shutil.rmtree(TMP, ignore_errors=True)
print("FAILED: " + ", ".join(fails) if fails else "all card suggestion checks passed")
sys.exit(1 if fails else 0)
