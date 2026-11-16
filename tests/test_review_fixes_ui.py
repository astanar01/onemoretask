"""Small page fixes from a code review, each checked in the real index.html + i18n.js in headless Chrome over raw CDP,
with /api faked by a stub server on a free port (never the live board, never .task_board; HOME is a temp dir):
1. Two open tabs: when both move a finished card at once, the tab whose save is refused reloads quietly (no red
   "Please redo your last change"); a refused save that held a real edit still shows the red message.
2. A poll redraw waits while a column name is being typed, so the cursor stays in the box; it redraws once you leave.
3. Another window's save keeps a half-typed subtask in the open card; opening another card or Enter clears it.
4. The repo dialog shows why the folder size check failed instead of an empty box.
5. A failed memory clean-up action shows a line in the window.
6. French: t() and translateDom only use the dictionary's own lines ("constructor" comes back as typed).

Run: python3 tests/test_review_fixes_ui.py  (stdlib + node 22+ + Google Chrome)."""
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
TMP = tempfile.mkdtemp(prefix="review-fixes-ui-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}
T0 = "2026-10-15T10:00:00+00:00"
COLS = [{"id": "todo", "name": "To Do", "done": False}, {"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "rev", "name": "Review", "done": False, "role": "review"}, {"id": "done", "name": "Done", "done": True}]
TASKS = [{"id": "w1", "title": "Working card", "notes": "", "priority": "", "tags": [], "column": "prog", "parent": None, "order": 0,
          "created": T0, "updated": T0, "agent": {"id": "s1", "started": T0}},
         {"id": "n1", "title": "Draft card", "notes": "", "priority": "", "tags": [], "column": "todo", "parent": None, "order": 0,
          "created": T0, "updated": T0}]
BOARD = {"version": 1, "columns": COLS, "tasks": TASKS}
st = {"rev": 1, "phase": "working", "polls": 0, "puts": []}
lock = threading.Lock()


class Stub(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Board-Rev", str(st["rev"]))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/finish":
            st["phase"] = "finished"
            return self.send_json({})
        if p == "/bump":   # another window saved: same board, newer rev
            st["rev"] += 1
            return self.send_json({})
        if p == "/puts":
            return self.send_json(st["puts"])
        if p == "/api/projects":
            return self.send_json([{"id": "demo", "name": "demo", "path": TMP, "board": os.path.join(TMP, ".task_board")}])
        if p == "/api/tasks":
            return self.send_json(BOARD)
        if p == "/api/agents":
            st["polls"] += 1   # every poll brings a new line, so every poll redraws
            log = [{"from": "you", "status": "launch", "message": "go", "at": T0},
                   {"from": "claude", "status": "progress", "message": f"step {st['polls']}", "at": T0}]
            if st["phase"] == "finished":
                log.append({"from": "claude", "status": "done", "message": "done", "at": "2026-10-15T11:00:00+00:00"})
            return self.send_json({"w1": {"phase": st["phase"], "log": log}})
        if p == "/api/memory-cleanup":
            return self.send_json({"review": [{"id": "m1", "concept": "Old note", "names": ["gone.txt"]}],
                                   "removed": [], "earlier": [], "renamed": []})
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
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        m = self.headers.get("If-Match")
        with lock:   # like server.do_PUT: a save based on an older board is refused
            if m is not None and m != str(st["rev"]):
                st["puts"].append(["409", m])
                return self.send_json({"error": "The board changed in another window"}, 409)
            BOARD.clear()
            BOARD.update(body)
            st["rev"] += 1
            st["puts"].append(["ok", m])
        return self.send_json({"ok": True})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        p = self.path.split("?")[0]
        if p == "/api/git/scan":
            return self.send_json({"error": "scan blew up"}, 500)
        if p == "/api/memory-cleanup":
            return self.send_json({"error": "disk full"}, 500)
        return self.send_json({"ok": True})


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


DRIVER = r"""
import { spawn } from 'node:child_process';
const [url, chrome, dport, profile] = process.argv.slice(2);
const proc = spawn(chrome, ['--headless=new', `--remote-debugging-port=${dport}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', 'about:blank'], { stdio: 'ignore' });
const quit = (code) => { try { proc.kill(); } catch {} setTimeout(() => process.exit(code), 300); };
let step = 'start';
setTimeout(() => { console.log(JSON.stringify({ error: 'HUNG driver at ' + step })); quit(2); }, 90000);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const errors = [];
for (let i = 0; i < 75; i++) { try { await fetch(`http://127.0.0.1:${dport}/json`); break; } catch { await sleep(200); } }
await fetch(`http://127.0.0.1:${dport}/json/new?about:blank`, { method: 'PUT' });
await sleep(500);
const pages = (await (await fetch(`http://127.0.0.1:${dport}/json`)).json()).filter(t => t.type === 'page');
async function tab(t) {
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r, { once: true }));
  let id = 0; const pend = new Map();
  ws.addEventListener('message', ({ data }) => {
    const m = JSON.parse(data);
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
    else if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  });
  const send = (method, params = {}) => new Promise(r => { pend.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
  const ev = async (e) => { const r = await send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true, replMode: true }); return r.result.exceptionDetails ? { exc: r.result.exceptionDetails.exception?.description } : r.result.result.value; };
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 800, deviceScaleFactor: 1, mobile: false });
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('task-board:view', 'board'); } catch (e) {}` });
  await send('Page.navigate', { url });
  for (let i = 0; i < 100; i++) { await sleep(100); if (await ev(`typeof polledOnce !== 'undefined' && polledOnce && document.querySelectorAll('.card').length > 0`) === true) break; }
  // No 4 s timer: the test decides when each tab polls.
  await ev(`clearTimeout(pollTimer); pollAgents = ((f) => async function () { const r = await f.apply(this, arguments); clearTimeout(pollTimer); return r; })(pollAgents); true`);
  return { ev, send };
}
const A = await tab(pages[0]), B = await tab(pages[1]);
const out = {};
const status = (T) => T.ev(`({ text: $('#status').textContent, err: $('#status').className === 'err' })`);
await sleep(500);

step = '1.';
// 1. Both tabs see the session finish at the same moment and both move the card.
await fetch(url + 'finish');
await Promise.all([A.ev(`await pollAgents(); true`), B.ev(`await pollAgents(); true`)]);
await sleep(2000);
out.twoTabs = { A: await status(A), B: await status(B), colA: await A.ev(`task('w1').column`), colB: await B.ev(`task('w1').column`),
  puts: await (await fetch(url + 'puts')).json() };
// Negative control: B's save holds a real edit and is refused.
await fetch(url + 'bump');
await B.ev(`await (async () => { const x = task('n1'); x.title = 'Typed by the user'; touch(x); await flush(); })(); true`);
await sleep(500);
out.userEdit409 = await status(B);

step = '2.';
// 2. Column rename while polls redraw.
// Bringing the tab to the front starts a catch-up poll: let it land first, then take the board as saved.
await A.send('Page.bringToFront');
await sleep(1500);
await A.ev(`await reloadBoard(true)`);
await sleep(300);
await A.ev(`window.oldCol = document.querySelector('[data-col="todo"]'); const i = document.querySelector('[data-col="todo"] .name'); i.focus(); i.select(); true`);
await A.send('Input.insertText', { text: 'Backlog' });
await A.ev(`await pollAgents(); true`); await A.ev(`await pollAgents(); true`);
await sleep(700);
out.renaming = await A.ev(`({ focus: document.activeElement.matches('[data-col="todo"] .name'), value: document.activeElement.value,
  sameBox: window.oldCol.isConnected, name: state.columns.find(c => c.id === 'todo').name })`);
await A.ev(`document.activeElement.blur(); true`);
await sleep(800);
out.afterRename = await A.ev(`({ name: state.columns.find(c => c.id === 'todo').name, redrawn: !window.oldCol.isConnected })`);
await A.ev(`window.oldCol = document.querySelector('[data-col="todo"]'); await pollAgents(); true`);
await sleep(700);
out.pollRedrawsWhenNotTyping = await A.ev(`!window.oldCol.isConnected`);

step = '3.';
// 3. Half-typed subtask in the open card while another window saves.
await A.ev(`openDetail('n1'); true`);
await sleep(300);
await A.ev(`$('#d-newsub').focus(); true`);
await A.send('Input.insertText', { text: 'half typed subtask' });
await fetch(url + 'bump');
await A.ev(`await pollAgents(); true`);
await sleep(500);
out.newsub = { kept: await A.ev(`$('#d-newsub').value`), status: (await status(A)).text };
await A.ev(`openDetail('w1'); true`); await sleep(200);
await A.ev(`openDetail('n1'); true`); await sleep(200);
out.newsub.otherCardThenBack = await A.ev(`$('#d-newsub').value`);
await A.ev(`const ns = $('#d-newsub'); ns.value = 'sub one'; ns.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); true`);
await sleep(300);
out.newsub.afterEnter = await A.ev(`({ value: $('#d-newsub').value, kids: children('n1').map(k => k.title) })`);
await A.ev(`closeDetail(); true`);

step = '4.';
// 4. Repo dialog when the folder size check fails.
await A.ev(`askRepo(task('n1'), false); true`);
await sleep(600);
out.repoSizes = await A.ev(`$('#repo-sizes').textContent`);
await A.ev(`closeRepo(false); true`);

step = '5.';
// 5. Memory clean-up action that fails.
await A.ev(`await openMemory(); true`);
const memBtn = `document.querySelector('#cl-body [data-mem-action="remove"]')`;
out.memButtonShown = await A.ev(`!!${memBtn}`);
await A.ev(`${memBtn}.click(); true`);
await sleep(600);
out.memory = await A.ev(`({ line: ($('#cl-body .mem-err') || {}).textContent || null, red: !!$('#cl-body .mem-err') && getComputedStyle($('#cl-body .mem-err')).color, enabled: !${memBtn}.disabled })`);

step = '6.';
// 6. French lookups.
out.fr = await A.ev(`(() => {
  LANG = 'fr';
  const r = { t: [t('constructor'), t('toString'), t('valueOf'), t('To Do'), t('constructor', { x: 1 })] };
  const d = document.createElement('div');
  d.innerHTML = '<span>constructor</span><b data-i18n>valueOf</b><i title="hasOwnProperty">To Do</i>';
  translateDom(d); r.dom = d.innerHTML;
  const c = state.columns.find(c => c.id === 'todo'), was = c.name; c.name = 'constructor'; render();
  r.column = document.querySelector('[data-col="todo"] .name').value;
  c.name = was; LANG = 'en'; render();
  return r;
})()`);
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
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome")],
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
    check("no page errors", res["errors"], [])
    tt = res["twoTabs"]
    check("two tabs: one save was refused (the race happened)", [x[0] for x in tt["puts"][:2]], ["ok", "409"])
    check("two tabs: both show the card in Review", (tt["colA"], tt["colB"]), ("rev", "rev"))
    check("two tabs: the first tab saved", tt["A"], {"text": "Saved", "err": False})
    check("two tabs: the refused tab reloads quietly", tt["B"], {"text": "Board updated from another window", "err": False})
    check("a refused save with a real edit still asks to redo it", res["userEdit409"],
          {"text": "The board changed in another window, so it was reloaded. Please redo your last change.", "err": True})
    check("rename: cursor and box survive two poll redraws", res["renaming"],
          {"focus": True, "value": "Backlog", "sameBox": True, "name": "To Do"})
    check("rename: leaving the box saves it and redraws", res["afterRename"], {"name": "Backlog", "redrawn": True})
    check("control: a poll redraws when nothing is being typed", res["pollRedrawsWhenNotTyping"], True)
    ns = res["newsub"]
    check("subtask: the reload from another window happened", ns["status"], "Board updated from another window")
    check("subtask: half-typed text kept", ns["kept"], "half typed subtask")
    check("subtask: another card, then back, starts empty", ns["otherCardThenBack"], "")
    check("subtask: Enter adds it and empties the box", ns["afterEnter"], {"value": "", "kids": ["sub one"]})
    check("repo dialog: size check failure is shown", res["repoSizes"], "Could not measure the folder: scan blew up")
    check("memory: the window lists a note to remove", res["memButtonShown"], True)
    mem = res["memory"] if "exc" not in res["memory"] else {"line": res["memory"]["exc"], "red": None, "enabled": None}
    check("memory: failed action shows a line", mem["line"], "Could not change the memory note: disk full")
    check("memory: the line is red", mem["red"] in ("rgb(192, 57, 43)", "rgb(232, 104, 92)"), True)
    check("memory: the button can be clicked again", mem["enabled"], True)
    if "exc" in res["fr"]:
        res["fr"] = {"t": res["fr"]["exc"], "dom": None, "column": None}
    check("fr: t() keeps prototype names", res["fr"]["t"], ["constructor", "toString", "valueOf", "À faire", "constructor"])
    check("fr: translateDom keeps prototype names", res["fr"]["dom"],
          '<span>constructor</span><b data-i18n="">valueOf</b><i title="hasOwnProperty">À faire</i>')
    check("fr: a column named constructor shows its name", res["fr"]["column"], "constructor")
    print("ALL PASS" if not fails else f"{fails} FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
