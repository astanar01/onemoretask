"""The Plan page: make a milestone, write tasks, get Claude's ideas, send ticked tasks and then the rest to To Do as
one milestone card with subtasks, follow the done count, send a deleted card again, reorder by drag, and keep it all
across a reload. A poll never takes the cursor out of a field being typed in.
Drives the real index.html + i18n.js in headless Chrome over raw CDP, with /api faked by a stub server on a free
port that keeps what the page saves (never the live board, never .task_board).

Run: python3 tests/test_plan_ui.py  (stdlib + node 22+ + Google Chrome; PLAN_SHOTS=dir saves screenshots)."""
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
TMP = tempfile.mkdtemp(prefix="plan-ui-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}

BOARD = {"version": 1, "columns": [{"id": "c1", "name": "To Do", "done": False},
                                   {"id": "c2", "name": "In Progress", "done": False, "role": "progress"},
                                   {"id": "c3", "name": "Review", "done": False, "role": "review"},
                                   {"id": "c4", "name": "Done", "done": True}],
         "tasks": [{"id": "t0", "title": "Old card", "notes": "", "priority": "", "tags": [], "column": "c1", "parent": None,
                    "order": 0, "created": "2026-10-20T10:00:00+00:00", "updated": "2026-10-20T10:00:00+00:00"}]}
STORE = {"board": BOARD, "rev": 1, "suggest": []}


class Stub(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Board-Rev", str(STORE["rev"]))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/projects":
            return self.send_json([{"id": "demo", "name": "demo", "path": TMP, "board": os.path.join(TMP, ".task_board")}])
        if p == "/api/tasks":
            return self.send_json(STORE["board"])
        if p == "/__test/board":
            return self.send_json(STORE)
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
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.startswith("/api/tasks"):
            STORE["board"] = body
            STORE["rev"] += 1
            self.send_response(204)
            self.send_header("X-Board-Rev", str(STORE["rev"]))
            self.end_headers()
            return
        return self.send_json({"ok": True})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.startswith("/api/plan/suggest"):
            STORE["suggest"].append(body)
            return self.send_json({"tasks": [{"title": "Idea one", "notes": "From Claude."}, {"title": "Idea two", "notes": ""}]})
        return self.send_json({"ok": True})


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
setTimeout(() => { console.log(JSON.stringify({ error: 'HUNG driver' })); quit(2); }, 100000);
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
  if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') errors.push('log: ' + m.params.entry.text + ' ' + (m.params.entry.url || ''));
});
const send = (method, params = {}) => new Promise(r => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, replMode: true }); return r.result.exceptionDetails ? { exc: r.result.exceptionDetails.exception?.description } : r.result.result.value; };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const shot = async (name) => { if (!shots) return; const fs = await import('node:fs'); const r = await send('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(`${shots}/${name}.png`, Buffer.from(r.result.data, 'base64')); };
await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 860, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url });
const waitReady = async () => {
  for (let i = 0; i < 100; i++) { await sleep(100); if (await ev(`typeof polledOnce !== 'undefined' && polledOnce && !!task('t0')`) === true) return true; }
  return false;
};
const out = { ready: await waitReady() };
if (!out.ready) { out.errors = errors; console.log(JSON.stringify(out)); quit(0); }

// Helpers in the page: type like a user (value + input event), press Enter, click, read the plan.
const HELPERS = `window.__type = (el, v) => { el.focus(); el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); };
window.__enter = (el) => el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
window.__rows = () => [...document.querySelectorAll('.pl-row')].map((r) => ({ title: (r.querySelector('input[type=text]') || {}).value ?? r.querySelector('.sent').textContent,
  chip: r.querySelector('.pl-chip')?.textContent ?? '', sug: r.classList.contains('sug'), tickOff: r.querySelector('.pl-tick').disabled }));
window.__srows = () => [...document.querySelectorAll('.pl-row')].map((r) => [(r.querySelector('input[type=text]') || {}).value ?? r.querySelector('.sent').textContent, r.classList.contains('pl-subrow')]);
window.__rowEl = (title) => [...document.querySelectorAll('.pl-row')].find((r) => ((r.querySelector('input[type=text]') || {}).value ?? r.querySelector('.sent').textContent) === title);
window.__ms = () => state.milestones[0];
window.__big = () => state.tasks.filter((x) => x.milestone);
true`;
await ev(HELPERS);

const sel = `document.querySelector('#layout')`;
const planBtn = `document.querySelector('#plan-btn')`;
out.noPlanLayout = await ev(`[...${sel}.options].map((o) => o.value)`);
out.empty = await ev(`${planBtn}.click(); document.querySelector('.pl-empty')?.textContent.trim()`);
out.boardClass = await ev(`[document.querySelector('#board').classList.contains('plan'), document.querySelector('#board').classList.contains('list'), ${planBtn}.getAttribute('aria-pressed')]`);
out.make = await ev(`document.querySelector('.pl-new').click();
const focused = document.activeElement === document.querySelector('.pl-title');
__type(document.querySelector('.pl-title'), 'Login works');
__type(document.querySelector('.pl-notes'), 'People can log in with email.');
const side = document.querySelector('.pl-item .t').textContent;
for (const v of ['Make the users table', 'Add a login form', 'Check the password']) { const a = document.querySelector('.pl-add'); a.value = v; __enter(a); }
({ focused, side, rows: __rows(), addFocused: document.activeElement === document.querySelector('.pl-add'), n: state.milestones.length })`);
// A poll redraw while the cursor is in a field must leave it there.
out.poll = await ev(`const f = document.querySelector('.pl-row input[type=text]'); f.focus(); state.tasks[0].title = 'Old card!'; render();
({ same: document.activeElement === f, inDom: document.contains(f) })`);
await ev(`document.activeElement.blur(); await new Promise((r) => setTimeout(r, 50)); true`);
out.suggest = await ev(`document.querySelector('.pl-suggest').click();
for (let i = 0; i < 50 && document.querySelectorAll('.pl-row').length < 5; i++) await new Promise((r) => setTimeout(r, 100));
__rows()`);
await sleep(100);
await shot('plan-suggested');
// Tick 1 and 4, send those; then send the rest.
out.ticked = await ev(`const ticks = () => document.querySelectorAll('.pl-tick');
ticks()[0].click(); ticks()[3].click();
const label = document.querySelector('.pl-send-ticked').textContent;
document.querySelector('.pl-send-ticked').click();
const big = __big();
({ label, bigCount: big.length, bigCol: big[0]?.column, bigTitle: big[0]?.title, bigNotes: big[0]?.notes,
   kids: children(big[0]?.id).map((k) => [k.title, k.column, k.notes]), msCard: __ms().card === big[0]?.id, rows: __rows(),
   sendAll: document.querySelector('.pl-send-all').textContent })`);
await shot('plan-ticked-sent');
out.all = await ev(`document.querySelector('.pl-send-all').click();
const big = __big();
({ bigCount: big.length, kids: children(big[0].id).map((k) => k.title), rows: __rows(), progress: document.querySelector('.pl-head .progress').textContent.trim(),
   sendAllOff: document.querySelector('.pl-send-all').disabled })`);
// A card moved to Done counts; a deleted card can be sent again under the same milestone card.
out.done = await ev(`const big = __big()[0]; const kids = children(big.id);
kids[0].column = 'c4'; render();
const p1 = document.querySelector('.pl-head .progress').textContent.trim(), side = document.querySelector('.pl-item .progress').textContent.trim();
state.tasks = state.tasks.filter((x) => x.id !== kids[1].id); render();
const rows = __rows(), sendAll = document.querySelector('.pl-send-all').textContent;
document.querySelector('.pl-send-all').click();
({ p1, side, gone: rows.map((r) => r.chip), goneTick: rows.map((r) => r.tickOff), sendAll, after: __rows().map((r) => r.chip),
   kids: children(big.id).length, bigCount: __big().length })`);
// The milestone card itself deleted: Send makes a new one.
out.bigGone = await ev(`const big = __big()[0];
state.tasks = state.tasks.filter((x) => x.id !== big.id && x.parent !== big.id); render();
const left = document.querySelector('.pl-send-all').textContent;
document.querySelector('.pl-send-all').click();
const nb = __big();
({ left, bigCount: nb.length, newId: nb[0]?.id !== big.id, kids: children(nb[0]?.id).length })`);
// Drag the third task above the first.
out.drag = await ev(`const rows = () => [...document.querySelectorAll('.pl-row')];
const before = __rows().map((r) => r.title);
const src = rows()[2], dst = rows()[0], dt = new DataTransfer(), r = dst.getBoundingClientRect();
const at = { bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.left + 10, clientY: r.top + 3 };
src.dispatchEvent(new DragEvent('dragstart', at));
dst.dispatchEvent(new DragEvent('dragover', at));
dst.dispatchEvent(new DragEvent('drop', at));
src.dispatchEvent(new DragEvent('dragend', at));
({ before, after: __rows().map((r) => r.title) })`);
await shot('plan-sent');
// Saved: the server's copy has the milestone; a reload comes back on the Plan page with it.
await ev(`await settled(); true`);
out.saved = await ev(`(await (await fetch('/__test/board')).json()).board.milestones?.map((m) => [m.title, m.tasks.length])`);
out.asked = await ev(`(await (await fetch('/__test/board')).json()).suggest`);
await send('Page.reload', {});
out.reloadReady = await waitReady();
await ev(HELPERS);
await sleep(200);
out.reload = await ev(`({ view, title: document.querySelector('.pl-title')?.value, rows: __rows().length })`);
// The Board shows the milestone card with its flag and subtasks.
out.board = await ev(`${planBtn}.click();
const card = document.querySelector('.card[data-id="' + __big()[0].id + '"]');
({ flag: !!card && card.textContent.includes('⚑'), progress: card?.querySelector('.progress')?.textContent.trim(), planGone: !document.querySelector('.plan'), pressed: ${planBtn}.getAttribute('aria-pressed') })`);
// Closing the Plan tool goes back to the layout it was opened from: the list here.
out.backToList = await ev(`${sel}.value = 'list'; ${sel}.dispatchEvent(new Event('change'));
${planBtn}.click(); const inPlan = !!document.querySelector('.plan');
${planBtn}.click();
const r = [inPlan, document.querySelector('#board').classList.contains('list'), ${sel}.value];
${sel}.value = 'board'; ${sel}.dispatchEvent(new Event('change')); r`);
await shot('plan-board');
out.del = await ev(`${planBtn}.click();
document.querySelector('.pl-delm').click();
const armed = document.querySelector('.pl-delm').textContent;
document.querySelector('.pl-delm').click();
({ armed, ms: state.milestones.length, cardsKept: __big().length, empty: !!document.querySelector('.pl-empty') })`);
// A real mouse, held down like a person's click, right after typing: leaving the field redraws the plan, and that
// redraw must not swallow the click. Synthetic .click() above never moves the focus, so it cannot catch this.
const press = async (selector) => {
  const r = await ev(`const b = document.querySelector('${selector}').getBoundingClientRect(); ({ x: b.x + b.width / 2, y: b.y + b.height / 2 })`);
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r.x, y: r.y });
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  await sleep(120);
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
  await sleep(150);
};
const enter = () => send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
await ev(`document.querySelector('.pl-new').click(); true`);
await send('Input.insertText', { text: 'Mouse test' });
await ev(`document.querySelector('.pl-add').focus(); true`);
await send('Input.insertText', { text: 'First' });
await enter();
await ev(`const f = document.querySelector('.pl-row input[type=text]'); f.focus(); f.setSelectionRange(f.value.length, f.value.length); true`);
await send('Input.insertText', { text: ' edited' });
await press('.pl-send-all');
const mouseKids = `const ms = state.milestones.find((m) => m.title === 'Mouse test'); const big = ms && ms.card && task(ms.card);
big ? children(big.id).map((c) => c.title) : null`;
out.mouseSend = await ev(mouseKids);
// A task typed in the add box without Enter is not lost when a button is pressed next.
await ev(`document.querySelector('.pl-add').focus(); true`);
await send('Input.insertText', { text: 'Second' });
out.addLabel = await ev(`document.querySelector('.pl-send-all').textContent`);
await press('.pl-send-all');
out.addSend = { kids: await ev(mouseKids), box: await ev(`document.querySelector('.pl-add').value`) };
// Subtasks: the + on a task opens a box under it; they go to the board under their task's card.
const subMs = `state.milestones.find((m) => m.title === 'Sub test')`;
await ev(`document.querySelector('.pl-new').click(); __type(document.querySelector('.pl-title'), 'Sub test');
for (const v of ['Parent A', 'Parent B']) { const a = document.querySelector('.pl-add'); a.value = v; __enter(a); }
document.activeElement.blur(); await new Promise((r) => setTimeout(r, 50)); true`);
out.subAdd = await ev(`__rowEl('Parent A').querySelector('.pl-addsub').click();
const focused = document.activeElement === document.querySelector('.pl-sub-add');
for (const v of ['A1', 'A2']) { const b = document.querySelector('.pl-sub-add'); b.value = v; __enter(b); }
({ focused, keep: document.activeElement === document.querySelector('.pl-sub-add'), rows: __srows(), sendAll: document.querySelector('.pl-send-all').textContent })`);
await shot('plan-subtasks');
out.subEsc = await ev(`document.querySelector('.pl-sub-add').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
!document.querySelector('.pl-sub-add')`);
out.subTicked = await ev(`__rowEl('A2').querySelector('.pl-tick').click();
const label = document.querySelector('.pl-send-ticked').textContent;
document.querySelector('.pl-send-ticked').click();
const ms = ${subMs}, a = ms.tasks.find((d) => d.title === 'Parent A');
({ label, kids: children(ms.card).map((k) => k.title), aKids: children(a.card).map((k) => [k.title, k.column]), sendAll: document.querySelector('.pl-send-all').textContent })`);
out.subAll = await ev(`document.querySelector('.pl-send-all').click();
const ms = ${subMs}, a = ms.tasks.find((d) => d.title === 'Parent A');
({ kids: children(ms.card).map((k) => k.title), aKids: children(a.card).map((k) => k.title).sort(), sendAllOff: document.querySelector('.pl-send-all').disabled })`);
out.subLate = await ev(`__rowEl('Parent A').querySelector('.pl-addsub').click();
const b = document.querySelector('.pl-sub-add'); b.value = 'A3'; __enter(b);
const label = document.querySelector('.pl-send-all').textContent;
document.querySelector('.pl-send-all').click();
const ms = ${subMs}, a = ms.tasks.find((d) => d.title === 'Parent A');
({ label, aKids: children(a.card).map((k) => k.title).sort() })`);
out.subDrag = await ev(`const dragTo = (src, dst) => { const dt = new DataTransfer(), r = dst.getBoundingClientRect();
  const at = { bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.left + 10, clientY: r.top + 3 };
  src.dispatchEvent(new DragEvent('dragstart', at)); dst.dispatchEvent(new DragEvent('dragover', at));
  dst.dispatchEvent(new DragEvent('drop', at)); src.dispatchEvent(new DragEvent('dragend', at)); };
dragTo(__rowEl('A3'), __rowEl('A1'));
const after = __srows().map((r) => r[0]);
dragTo(__rowEl('A3'), __rowEl('Parent B'));
({ after, refused: __srows().map((r) => r[0]) })`);
out.subRe = await ev(`const ms = ${subMs}, a = ms.tasks.find((d) => d.title === 'Parent A'), old = a.card;
state.tasks = state.tasks.filter((x) => x.id !== old); render();
const label = document.querySelector('.pl-send-all').textContent;
document.querySelector('.pl-send-all').click();
({ label, fresh: a.card !== old, aKids: children(a.card).map((k) => k.title).sort(), inBig: task(a.card).parent === ms.card })`);
out.subDel = await ev(`const a = document.querySelector('.pl-add'); a.value = 'Parent C'; __enter(a);
__rowEl('Parent C').querySelector('.pl-addsub').click();
const b = document.querySelector('.pl-sub-add'); b.value = 'C1'; __enter(b);
const had = __srows().map((r) => r[0]).includes('C1');
__rowEl('Parent C').querySelector('.pl-del').click();
({ had, rows: __srows().map((r) => r[0]) })`);
// A subtask typed without Enter is not lost when a real mouse presses Send all next.
await ev(`__rowEl('Parent B').querySelector('.pl-addsub').click(); true`);
await send('Input.insertText', { text: 'B1' });
await press('.pl-send-all');
out.subMouse = await ev(`const ms = ${subMs}, b = ms.tasks.find((d) => d.title === 'Parent B'); children(b.card).map((k) => k.title)`);
await shot('plan-subtasks-sent');
// A task in Done with a subtask still open does not count as done.
out.subDone = await ev(`const ms = ${subMs}, dc = state.columns.find((c) => c.done).id;
ms.tasks.forEach((d) => (task(d.card).column = dc));
const before = planCount(ms).done;
ms.tasks.forEach((d) => planSubs(d).forEach((s) => (task(s.card).column = dc)));
({ before, after: planCount(ms).done })`);
out.errors = errors;
console.log(JSON.stringify(out));
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
    if not os.path.exists(CHROME):
        print(f"SKIP: Google Chrome not found at {CHROME} (set CHROME=)")
        return 0
    if not NODE or not os.path.exists(NODE):
        print("SKIP: node not found (set NODE=)")
        return 0
    port, dport = free_port(), free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    driver = os.path.join(TMP, "driver.mjs")
    open(driver, "w").write(DRIVER)
    shots = os.environ.get("PLAN_SHOTS", "")
    if shots:
        os.makedirs(shots, exist_ok=True)
    try:
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome"),
                            os.path.abspath(shots) if shots else ""], capture_output=True, text=True, timeout=120)
    finally:
        srv.shutdown()
    lines = p.stdout.strip().splitlines()
    try:
        res = json.loads(lines[-1])
    except Exception:
        print("FAIL driver gave no result\nstdout:", p.stdout[-2000:], "\nstderr:", p.stderr[-2000:])
        return 1
    if "error" in res:
        print("FAIL", res["error"])
        return 1
    check("page loaded and polled", res["ready"])
    for e in res.get("errors", []):
        print("     ", e[:300])
    check(f"no console errors or exceptions ({len(res.get('errors', []))})", len(res.get("errors", [])), 0)
    if not res["ready"]:
        return 1
    for k, v in res.items():
        if isinstance(v, dict) and "exc" in v:
            print(f"FAIL step {k} threw: {v['exc']}")
            return 1

    check("empty Plan page explains what to do", (res["empty"] or "").startswith("Make a milestone"))
    check("the Layout menu has only Board and List", res["noPlanLayout"], ["board", "list"])
    check("the Plan button opens the Plan tool and shows pressed", res["boardClass"], [True, False, "true"])
    m = res["make"]
    check("New milestone puts the cursor in its name", m["focused"])
    check("typing the name updates the left list at once", m["side"], "Login works")
    check("three tasks added with Enter", [r["title"] for r in m["rows"]], ["Make the users table", "Add a login form", "Check the password"])
    check("the add box keeps the cursor after Enter", m["addFocused"])
    check("a poll redraw keeps the cursor in the field", res["poll"], {"same": True, "inDom": True})
    s = res["suggest"]
    check("Claude's ideas are added as dashed suggested drafts", [(r["title"], r["sug"], r["chip"]) for r in s[3:]],
          [("Idea one", True, "Suggested"), ("Idea two", True, "Suggested")])
    t = res["ticked"]
    check("Send ticked counts the ticked tasks", t["label"], "Send ticked (2)")
    check("one milestone card in To Do with its name and goal", (t["bigCount"], t["bigCol"], t["bigTitle"], t["bigNotes"]),
          (1, "c1", "Login works", "People can log in with email."))
    check("only the ticked tasks became its subtasks, in To Do", t["kids"],
          [["Make the users table", "c1", ""], ["Idea one", "c1", "From Claude."]])
    check("the milestone remembers its card", t["msCard"])
    check("sent rows show their column and lock their tick box", [(r["chip"], r["tickOff"]) for r in t["rows"]],
          [("To Do", True), ("", False), ("", False), ("To Do", True), ("Suggested", False)])
    check("Send all counts what is left", t["sendAll"], "Send all (3)")
    a = res["all"]
    check("Send all reuses the same milestone card", a["bigCount"], 1)
    check("all five tasks are its subtasks, in plan order", a["kids"],
          ["Make the users table", "Idea one", "Add a login form", "Check the password", "Idea two"])
    check("done count with nothing done", a["progress"], "0 of 5 done")
    check("Send all is off when everything is on the board", a["sendAllOff"])
    d = res["done"]
    check("a card in Done moves the count", (d["p1"], d["side"]), ("1 of 5 done", "1 of 5 done"))
    check("a deleted card shows Not on the board", d["gone"].count("Not on the board"), 1)
    check("its tick box opens again", d["goneTick"].count(False), 1)
    check("Send all offers it again", d["sendAll"], "Send all (1)")
    check("sent again under the same milestone card", (d["kids"], d["bigCount"], "Not on the board" in d["after"]), (5, 1, False))
    b = res["bigGone"]
    check("milestone card deleted: everything can go again", b["left"], "Send all (5)")
    check("a new milestone card is made with all five", (b["bigCount"], b["newId"], b["kids"]), (1, True, 5))
    g = res["drag"]
    check("drag moves the third task to the top", g["after"], [g["before"][2], g["before"][0], g["before"][1]] + g["before"][3:])
    check("the server's copy holds the milestone", res["saved"], [["Login works", 5]])
    check("Suggest sent the goal and the tasks already listed", res["asked"], [{"p": "demo", "title": "Login works", "notes": "People can log in with email.",
          "have": ["Make the users table", "Add a login form", "Check the password"]}])
    check("reload comes back on the Plan page with it", (res["reloadReady"], res["reload"]),
          (True, {"view": "plan", "title": "Login works", "rows": 5}))
    bo = res["board"]
    check("the board shows the milestone card with its flag and subtasks", (bo["flag"], bo["progress"], bo["planGone"]), (True, "0/5", True))
    check("clicking Plan again closes it, button no longer pressed", bo["pressed"], "false")
    check("closing Plan goes back to the list when opened from the list", res["backToList"], [True, True, "list"])
    x = res["del"]
    check("Delete milestone asks for a second click", x["armed"], "Really delete? Click again")
    check("deleting the milestone keeps its cards", (x["ms"], x["cardsKept"], x["empty"]), (0, 1, True))
    check("a real click on Send all right after typing a task name works", res["mouseSend"], ["First edited"])
    check("Send all counts a task still in the add box", res["addLabel"], "Send all (1)")
    check("a task left in the add box is sent too, and the box empties", res["addSend"],
          {"kids": ["First edited", "Second"], "box": ""})
    s = res["subAdd"]
    check("the + on a task opens a subtask box with the cursor in it", s["focused"])
    check("subtasks added with Enter sit under their task; the box keeps the cursor", (s["rows"], s["keep"]),
          ([["Parent A", False], ["A1", True], ["A2", True], ["Parent B", False]], True))
    check("Send all counts subtasks too", s["sendAll"], "Send all (4)")
    check("Escape closes the subtask box", res["subEsc"])
    s = res["subTicked"]
    check("a ticked subtask brings its task along", s["label"], "Send ticked (2)")
    check("the task goes under the milestone card, the subtask under the task's card",
          (s["kids"], s["aKids"], s["sendAll"]), (["Parent A"], [["A2", "c1"]], "Send all (2)"))
    s = res["subAll"]
    check("Send all sends the rest of the subtasks under their task", (s["kids"], s["aKids"], s["sendAllOff"]),
          (["Parent A", "Parent B"], ["A1", "A2"], True))
    check("a subtask added to a task already on the board goes under its card", res["subLate"],
          {"label": "Send all (1)", "aKids": ["A1", "A2", "A3"]})
    check("a subtask drags among its task's subtasks", res["subDrag"]["after"], ["Parent A", "A3", "A1", "A2", "Parent B"])
    check("a subtask cannot be dragged out of its task", res["subDrag"]["refused"], ["Parent A", "A3", "A1", "A2", "Parent B"])
    check("a task's deleted card is made again and keeps its subtasks", res["subRe"],
          {"label": "Send all (1)", "fresh": True, "aKids": ["A1", "A2", "A3"], "inBig": True})
    check("removing a task removes its subtasks", (res["subDel"]["had"], res["subDel"]["rows"]),
          (True, ["Parent A", "A3", "A1", "A2", "Parent B"]))
    check("a subtask left in its box is sent by a real click on Send all", res["subMouse"], ["B1"])
    check("a task counts as done only once its subtasks are done", res["subDone"], {"before": 0, "after": 2})
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{passes} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
