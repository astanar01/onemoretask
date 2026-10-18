"""The Conversation tab draws one horizontal decision tree from every log entry that carries a decision: a later entry
with the same query replaces the earlier node, each decision hangs off the previous one's chosen option, inline log
entries show only the plain line, and a poll that brings a new decision grows the tree without a reload.
Drives the real index.html + i18n.js in headless Chrome over raw CDP, with /api faked by a stub server on a free
port (never the live board, never .task_board). The log is built with reports.py into a temp board.

Run: python3 tests/test_decision_tree_ui.py  (stdlib + node 22+ + Google Chrome)."""
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

TMP = tempfile.mkdtemp(prefix="decision-tree-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}
BOARD_DIR = os.path.join(TMP, ".task_board")


def task(tid, title):
    return {"id": tid, "title": title, "notes": "", "priority": "", "tags": [], "column": "c2", "parent": None, "order": 0,
            "created": "2026-10-15T11:00:00+00:00", "updated": "2099-01-01T00:00:00+00:00",
            "agent": {"id": tid, "started": "2026-10-15T11:47:00+00:00"}}


BOARD = {"version": 1, "columns": [{"id": "c1", "name": "To Do", "done": False},
                                   {"id": "c2", "name": "In Progress", "done": False, "role": "progress"},
                                   {"id": "c3", "name": "Review", "done": False, "role": "review"},
                                   {"id": "c4", "name": "Done", "done": True}],
         "tasks": [task("t1", "Decisions"), task("t2", "No decisions")]}

A = {"query": "Tree or list?", "options": [{"label": "List", "why": "simple"}, {"label": "Tree", "why": "shows paths"},
                                           {"label": "Both", "why": "costly"}], "chosen": 1, "by": "agent", "why": "clearer"}
B_OPEN = {"query": "Which side?", "options": [{"label": "Left", "why": ""}, {"label": "Right", "why": ""}], "chosen": None}
B_DONE = dict(B_OPEN, query="  Which side?  ", chosen=0, by="you", why="reads first")  # trimmed match replaces the open one
C = {"query": "Animate it?", "options": [{"label": "No", "why": ""}, {"label": "Yes", "why": "smooth"}], "chosen": 1}
# Grey's long why wraps, so growing the text pushes it into Accent below unless the tree is laid out again.
D = {"query": "Colour edges?", "options": [{"label": "Grey", "why": "quiet, keeps the eye on the boxes and not on the lines between them"},
                                           {"label": "Accent", "why": ""}], "chosen": None}


def add_decision(tid, raw):
    d = reports.check_decision(raw)
    reports.append(BOARD_DIR, tid, "progress", reports.decision_message(d), "claude", decision=d)


def log_of(tid):
    return json.load(open(reports._path(BOARD_DIR, tid)))


reports.append(BOARD_DIR, "t1", "progress", "Starting.", "claude")
for raw in (A, B_OPEN, B_DONE, C):
    add_decision("t1", raw)
reports.append(BOARD_DIR, "t2", "progress", "Plain work, no decisions.", "claude")
LOGS = {"t1": log_of("t1"), "t2": log_of("t2")}


def agents():
    return {tid: {"id": tid, "state": "working", "phase": "working", "reason": "", "log": LOGS[tid]} for tid in LOGS}


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
            return self.send_json([{"id": "demo", "name": "demo", "path": TMP, "board": BOARD_DIR}])
        if p == "/api/tasks":
            return self.send_json(BOARD)
        if p == "/api/agents":
            return self.send_json(agents())
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
        if self.path == "/__test/phase2":
            add_decision("t1", D)
            LOGS["t1"] = log_of("t1")
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
const [url, chrome, dport, profile] = process.argv.slice(2);
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
  if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') errors.push('log: ' + m.params.entry.text + ' ' + (m.params.entry.url || ''));
});
const send = (method, params = {}) => new Promise(r => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, replMode: true }); return r.result.exceptionDetails ? { exc: r.result.exceptionDetails.exception?.description } : r.result.result.value; };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 900, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url });
let ready = false;
for (let i = 0; i < 100 && !ready; i++) { await sleep(100); ready = await ev(`typeof polledOnce !== 'undefined' && polledOnce && !!task('t1')`) === true; }
const out = { ready };
if (!ready) { out.errors = errors; console.log(JSON.stringify(out)); quit(0); }

// Measures the tree as the page drew it. Options are flat siblings in no grouped order and carry no data-q:
// an option belongs to the nearest [data-q] ancestor if any, else to the nearest node on its left.
// Wrapped in a sync IIFE: the page's own globals (e.g. dtLog) would clash with top-level declarations.
const MEASURE = `(() => {
const dtT = document.querySelector('#d-dtree'), dtLog = document.querySelector('#d-log');
const dtNodes = [...document.querySelectorAll('#d-dtree .dt-node')];
const dtOpts = [...document.querySelectorAll('#d-dtree .dt-opt')];
const dtR = (e) => e.getBoundingClientRect();
const dtOwner = new Map();
for (const o of dtOpts) {
  const up = o.closest('[data-q]');
  if (up && dtT.contains(up)) { dtOwner.set(o, up.dataset.q); continue; }
  const left = dtNodes.filter((n) => dtR(n).left < dtR(o).left).sort((a, b) => dtR(b).left - dtR(a).left)[0];
  dtOwner.set(o, left ? left.dataset.q : null);
}
return {
  exists: !!dtT,
  inConv: !!(dtT && dtT.closest('#d-tab-conversation')),
  beforeLog: !!(dtT && dtLog && (dtT.compareDocumentPosition(dtLog) & Node.DOCUMENT_POSITION_FOLLOWING)),
  hidden: dtT ? dtT.hidden : null,
  overflowX: dtT ? getComputedStyle(dtT).overflowX : null,
  scrollW: dtT ? dtT.scrollWidth : 0, clientW: dtT ? dtT.clientWidth : 0,
  pageScrollW: document.documentElement.scrollWidth, innerW: innerWidth,
  nodes: dtNodes.map((n) => ({ q: n.dataset.q, open: n.classList.contains('open'), left: dtR(n).left, w: dtR(n).width,
    opts: dtOpts.filter((o) => dtOwner.get(o) === n.dataset.q).map((o) => ({ left: dtR(o).left, on: o.classList.contains('on'), off: o.classList.contains('off'), text: o.textContent.trim() })) })),
  optOn: document.querySelectorAll('#d-dtree .dt-opt.on').length,
  optOff: document.querySelectorAll('#d-dtree .dt-opt.off').length,
  edges: [...document.querySelectorAll('#d-dtree .dt-edge')].map((e) => ({ tag: e.tagName.toLowerCase(), on: e.classList.contains('on') })),
  dtreeAnywhere: document.querySelectorAll('.dtree').length,
  logTexts: [...document.querySelectorAll('#d-log > li')].map((li) => li.textContent),
};
})()`;

out.open = await ev(`openDetail('t1'); showTab('conversation'); true`);
await sleep(500);
out.p1 = await ev(MEASURE);
await ev(`fetch('/__test/phase2', { method: 'PUT' }).then(r => r.ok)`);
// pollAgents runs every 4 s and redraws the open card when /api/agents changed: wait for it, no reload.
const loadMark = await ev(`window.__dtMark = Math.random(); window.__dtMark`);
for (let i = 0; i < 100; i++) {
  await sleep(100);
  if (await ev(`document.querySelectorAll('#d-dtree .dt-node').length >= 4 && document.querySelector('#d-log').textContent.includes('Colour edges?')`)) break;
}
out.p2 = await ev(MEASURE);
out.sameLoad = await ev(`window.__dtMark`) === loadMark;
// Text size change on a quiet board: the handler alone must redo the layout. Laid out at Small first (Normal to
// Extra large grows a box less than the 12px gap), then measured in the same task as the change event, so no poll
// can redraw in between.
out.ts = await ev(`(() => {
applyTextSize('0.9'); showTab('conversation');
const tsEls = () => [...document.querySelectorAll('#d-dtree .dt-node, #d-dtree .dt-opt')];
const tsPos = () => tsEls().map((e) => e.style.top + '/' + e.style.left).join(' ') + ' svg ' + document.querySelector('#d-dtree svg').getAttribute('height');
const before = tsPos(), sel = document.querySelector('#text-size');
sel.value = '1.3'; sel.dispatchEvent(new Event('change'));
const rs = tsEls().map((e) => e.getBoundingClientRect()), overlaps = [];
for (let i = 0; i < rs.length; i++) for (let j = i + 1; j < rs.length; j++) {
  const a = rs[i], b = rs[j];
  if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) overlaps.push([i, j]);
}
return { ts: getComputedStyle(document.documentElement).getPropertyValue('--ts').trim(), n: rs.length, relaid: tsPos() !== before, overlaps };
})()`);
out.open2 = await ev(`openDetail('t2'); showTab('conversation'); true`);
await sleep(300);
out.p3 = await ev(`(() => { const e = document.querySelector('#d-dtree'); return { exists: !!e, hidden: e ? e.hidden : null }; })()`);
await sleep(200);
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


def flat(s):
    return "".join(s.split())


def layout_checks(tag, m, n_opts):
    nodes = m["nodes"]
    check(f"{tag} options per node", [len(n["opts"]) for n in nodes], n_opts)
    for n in nodes:
        lefts = [o["left"] for o in n["opts"]]
        check(f"{tag} {n['q']!r}: has its options", len(lefts) > 0)
        if lefts:
            check(f"{tag} {n['q']!r}: node left of its options", all(n["left"] < x for x in lefts))
            check(f"{tag} {n['q']!r}: options share one left x", max(lefts) - min(lefts) <= 1)
    check(f"{tag} each decision sits right of the one before", all(b["left"] > a["left"] for a, b in zip(nodes, nodes[1:])))
    check(f"{tag} no page-wide sideways scroll ({m['pageScrollW']} <= {m['innerW']})", m["pageScrollW"] <= m["innerW"])
    check(f"{tag} tree scrolls sideways inside itself (overflow-x {m['overflowX']!r})", m["overflowX"] in ("auto", "scroll"))
    check(f"{tag} every edge is an SVG path", all(e["tag"] == "path" for e in m["edges"]) and len(m["edges"]) > 0)


def main():
    if not os.path.exists(CHROME):
        print(f"SKIP: Google Chrome not found at {CHROME} (set CHROME=)")
        return 0
    if not NODE or not os.path.exists(NODE):
        print(f"SKIP: node not found (set NODE=)")
        return 0
    port, dport = free_port(), free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    driver = os.path.join(TMP, "driver.mjs")
    open(driver, "w").write(DRIVER)
    try:
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome")],
                           capture_output=True, text=True, timeout=90)
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
    check("page loaded, polled and has task t1", res["ready"])
    for e in res.get("errors", []):
        print("     ", e[:300])
    check(f"no console errors or exceptions ({len(res.get('errors', []))})", len(res.get("errors", [])), 0)
    if not res["ready"]:
        return 1
    check("panel opened on Conversation", res["open"])
    for k in ("p1", "p2", "ts", "p3"):
        if isinstance(res.get(k), dict) and "exc" in res[k]:
            print(f"FAIL measuring {k} threw: {res[k]['exc']}")
            return 1

    m = res["p1"]
    check("#d-dtree exists", m["exists"])
    check("#d-dtree is inside #d-tab-conversation", m["inConv"])
    check("#d-dtree comes before #d-log", m["beforeLog"])
    check("#d-dtree shown when the log has decisions", m["hidden"], False)
    check("3 query nodes (a repeated query is one node)", [n["q"] for n in m["nodes"]], ["Tree or list?", "Which side?", "Animate it?"])
    check("no node is open after B was decided", [n["open"] for n in m["nodes"]], [False, False, False])
    check(".dt-opt.on count", m["optOn"], 3)
    check(".dt-opt.off count (3-1)+(2-1)+(2-1)", m["optOff"], 4)
    check(".dt-edge.on count", sum(e["on"] for e in m["edges"]), 3)
    chosen = [[o["text"] for o in n["opts"] if o["on"]] for n in m["nodes"]]
    check("chosen option per node", [len(c) == 1 and c[0].startswith(w) for c, w in zip(chosen, ["Tree", "Left", "Yes"])], [True] * 3)
    layout_checks("phase 1", m, [3, 2, 2])
    check("no .dtree element anywhere", m["dtreeAnywhere"], 0)
    check("inline entry shows the plain decision line", any(flat("Decision: Tree or list? -> Tree") in flat(t) for t in m["logTexts"]))
    check("inline open entry shows its plain line", any(flat("Decision open: Which side?") in flat(t) for t in m["logTexts"]))

    m = res["p2"]
    check("poll brought decision D into the log (setup)", any(flat("Decision open: Colour edges?") in flat(t) for t in m["logTexts"]))
    check("poll grew the tree without a reload", res["sameLoad"])
    check("4 nodes after the poll", [n["q"] for n in m["nodes"]], ["Tree or list?", "Which side?", "Animate it?", "Colour edges?"])
    check("one open node, the new one", [n["open"] for n in m["nodes"]], [False, False, False, True])
    check("open node's options are neither on nor off", [o["on"] or o["off"] for o in m["nodes"][-1]["opts"]] if m["nodes"] else None, [False, False])
    check(".dt-edge.on count unchanged by an open decision", sum(e["on"] for e in m["edges"]), 3)
    layout_checks("phase 2", m, [3, 2, 2, 2])

    t = res["ts"]
    check("text size picker set --ts to 1.3 (setup)", t["ts"], "1.3")
    check("text size change redid the tree layout without a poll", t["relaid"])
    check(f"no two tree boxes overlap after a text size change ({t['n']} boxes)", t["overlaps"], [])

    check("#d-dtree hidden for a log without decisions", res["p3"]["hidden"], True)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{passes} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
