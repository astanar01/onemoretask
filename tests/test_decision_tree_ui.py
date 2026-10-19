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
await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 900, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url });
const waitReady = async () => {
  for (let i = 0; i < 100; i++) { await sleep(100); if (await ev(`typeof polledOnce !== 'undefined' && polledOnce && !!task('t1')`) === true) return true; }
  return false;
};
const ready = await waitReady();
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
// The tree scrolls inside its bordered box, not #d-dtree itself.
const dtBox = document.querySelector('#d-dtree .dt-box'), dtHead = document.querySelector('#d-dtree .dt-head');
const dtBs = dtBox ? getComputedStyle(dtBox) : null, dtBr = dtBox ? dtR(dtBox) : null;
// Every box must lie inside the laid-out .dt-wrap, and the box's scroll area must be just that wrap plus its padding
// (scrollWidth alone would grow to fit any stray box, so it cannot catch one).
const dtWrap = document.querySelector('#d-dtree .dt-wrap');
const dtOut = dtWrap ? [...dtNodes, ...dtOpts].filter((e) => {
  const r = dtR(e), w = dtR(dtWrap);
  return r.left < w.left - 0.5 || r.top < w.top - 0.5 || r.right > w.right + 0.5 || r.bottom > w.bottom + 0.5;
}).length : -1;
const dtPad = dtBs ? [parseFloat(dtBs.paddingLeft) + parseFloat(dtBs.paddingRight), parseFloat(dtBs.paddingTop) + parseFloat(dtBs.paddingBottom)] : [0, 0];
const dtArea = dtBox && dtWrap ? { scrollW: dtBox.scrollWidth, scrollH: dtBox.scrollHeight,
  wrapW: Math.ceil(dtWrap.offsetWidth + dtPad[0]), wrapH: Math.ceil(dtWrap.offsetHeight + dtPad[1]) } : null;
// Colours as 0-255 rgba arrays: color-mix() computes to color(srgb …), plain colours to rgb()/rgba().
const dtRgb = (c) => { const n = (c.match(/[\\d.]+/g) || []).map(Number);
  return c.startsWith('color(') ? [n[0] * 255, n[1] * 255, n[2] * 255, n.length > 3 ? n[3] : 1].map((v, i) => i < 3 ? Math.round(v) : v) : [n[0], n[1], n[2], n.length > 3 ? n[3] : 1]; };
const dtVar = (v) => { const p = document.createElement('span'); p.style.color = 'var(' + v + ')'; document.body.appendChild(p); const c = getComputedStyle(p).color; p.remove(); return dtRgb(c); };
const dtHs = dtHead ? getComputedStyle(dtHead) : null;
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
  inSum: !!(dtT && dtT.closest('#d-tab-summary')),
  firstInPane: !!(dtT && dtT.parentElement.firstElementChild === dtT),
  shown: !!(dtT && dtT.offsetParent),
  beforeLog: !!(dtT && dtLog && (dtT.compareDocumentPosition(dtLog) & Node.DOCUMENT_POSITION_FOLLOWING)),
  hidden: dtT ? dtT.hidden : null,
  hasBox: !!dtBox,
  head: dtHead ? { text: dtHead.textContent.trim(), svg: !!dtHead.querySelector('svg'), above: dtR(dtHead).bottom <= dtBr.top + 0.5,
    // chevron on the title's row: inside the button's rect, not flung out by the tree's own svg rule
    svgInRow: (() => { const s = dtHead.querySelector('svg'), h = dtR(dtHead); if (!s) return false; const r = dtR(s);
      return r.top >= h.top - 0.5 && r.bottom <= h.bottom + 0.5 && r.left >= h.left - 0.5 && r.right <= h.right + 0.5; })(),
    aria: dtHead.getAttribute('aria-expanded'),
    label: [...dtHead.childNodes].filter((c) => c.nodeType === 3).map((c) => c.textContent).join('').trim(),
    count: dtHead.querySelector('.dt-count')?.textContent.trim() ?? null,
    bg: dtRgb(dtHs.backgroundColor), color: dtRgb(dtHs.color), borderColor: dtRgb(dtHs.borderTopColor),
    borderW: parseFloat(dtHs.borderTopWidth), fontPx: parseFloat(dtHs.fontSize), weight: +dtHs.fontWeight,
    accent: dtVar('--accent'), muted: dtVar('--muted'), panelBg: dtRgb(getComputedStyle(dtT).backgroundColor) } : null,
  overflowX: dtBs ? dtBs.overflowX : null,
  scrollW: dtBox ? dtBox.scrollWidth : 0, clientW: dtBox ? dtBox.clientWidth : 0,
  scrollL: dtBox ? dtBox.scrollLeft : 0,
  border: dtBs ? ['Top', 'Right', 'Bottom', 'Left'].map((s) => [parseFloat(dtBs['border' + s + 'Width']), dtBs['border' + s + 'Style']]) : null,
  borderColor: dtBs ? dtBs.borderTopColor : null, bg: dtT ? getComputedStyle(dtT).backgroundColor : null,
  outside: dtOut, area: dtArea,
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
// Summary shows the same tree at the top of its tab; Conversation gets it back.
out.sum = await ev(`showTab('summary'); ${MEASURE}`);
out.sumBack = await ev(`showTab('conversation'); ${MEASURE}`);
// A card opened straight on Summary lays the tree out there (it was never measured in Conversation first).
out.sumFresh = await ev(`closeDetail(); dtKey = null; openDetail('t1'); showTab('summary'); ${MEASURE}`);
await ev(`showTab('conversation'); true`);
await ev(`fetch('/__test/phase2', { method: 'PUT' }).then(r => r.ok)`);
// pollAgents runs every 4 s and redraws the open card when /api/agents changed: wait for it, no reload.
const loadMark = await ev(`window.__dtMark = Math.random(); window.__dtMark`);
for (let i = 0; i < 100; i++) {
  await sleep(100);
  if (await ev(`document.querySelectorAll('#d-dtree .dt-node').length >= 4 && document.querySelector('#d-log').textContent.includes('Colour edges?')`)) break;
}
out.p2 = await ev(MEASURE);
out.sameLoad = await ev(`window.__dtMark`) === loadMark;
// Fold: the head hides the box with an animated height, the choice survives a redraw and a reload, and a hover
// zoom never outlives the fold. Timed inside the page so CDP round trips do not blur the mid-animation sample.
const FOLD = `(() => {
const fd = document.querySelector('#d-dtree');
const fdFold = () => fd.querySelector('.dt-fold'), fdHead = () => fd.querySelector('.dt-head');
return { shut: fd.classList.contains('shut'), aria: fdHead().getAttribute('aria-expanded'), title: fdHead().title,
  h: fdFold().getBoundingClientRect().height, vis: getComputedStyle(fdFold()).visibility,
  saved: localStorage.getItem(LS_KEY + ':dtshut'), zooms: document.querySelectorAll('.dt-zoom').length,
  countBg: getComputedStyle(fd.querySelector('.dt-count')).backgroundColor };
})()`;
const waitIn = (ms) => `await new Promise((r) => setTimeout(r, ${ms}));`;
out.fold0 = await ev(FOLD);
// A zoom is up when the head is clicked: the click must drop it.
await ev(`dtZoom(document.querySelector('#d-dtree .dt-node')); document.querySelectorAll('.dt-zoom').length`).then((n) => (out.zoomBeforeShut = n));
out.foldMid = await ev(`document.querySelector('#d-dtree .dt-head').click(); ${waitIn(100)} ${FOLD}`);
out.foldEnd = await ev(`${waitIn(350)} ${FOLD}`);
// Real hit testing while shut: whatever the pointer lands on where the first box was must not raise a zoom.
out.hoverShut = await ev(`(() => {
const hr = document.querySelector('#d-dtree .dt-node').getBoundingClientRect();
const hit = document.elementFromPoint(hr.left + hr.width / 2, hr.top + hr.height / 2);
if (hit) hit.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
return { hitBox: !!(hit && hit.closest('.dt-node, .dt-opt')), zooms: document.querySelectorAll('.dt-zoom').length };
})()`);
out.foldRedraw = await ev(`dtKey = null; renderDecisionTree(dtLog); ${FOLD}`);
await send('Page.reload', {});
out.reloadReady = await waitReady();
await ev(`openDetail('t1'); showTab('conversation'); true`);
await sleep(500);
out.foldReload = await ev(FOLD);
out.unfoldMid = await ev(`document.querySelector('#d-dtree .dt-head').click(); ${waitIn(100)} ${FOLD}`);
out.unfoldEnd = await ev(`${waitIn(350)} ${FOLD}`);
// Text size change on a quiet board: the handler alone must redo the layout. Laid out at Small first (Normal to
// Extra large grows a box less than the 8px gap), then measured in the same task as the change event, so no poll
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
// Hover zoom on a box whose text is too long for the tree's short strip: the copy must leave the strip and show
// every line inside the window. The long text is set on the box in the page only, then the tree is drawn again.
await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 600, deviceScaleFactor: 1, mobile: false });
await sleep(200);
out.zoom = await ev(`(() => {
const el = [...document.querySelectorAll('#d-dtree .dt-node')].pop();
el.querySelector('.dt-who').textContent = 'quiet, keeps the eye on the boxes and not on the lines between them. '.repeat(14);
const res = [];
for (const box of [el, document.querySelector('#d-dtree .dt-opt')]) {
  dtZoom(box);
  const z = document.querySelector('.dt-zoom'), r = z.getBoundingClientRect();
  const inside = (q) => r.top >= q.top - 0.5 && r.left >= q.left - 0.5 && r.bottom <= q.bottom + 0.5 && r.right <= q.right + 0.5;
  let shown = inside({ top: 0, left: 0, bottom: innerHeight, right: innerWidth });
  for (let p = z.parentElement; p; p = p.parentElement) if (getComputedStyle(p).overflow !== 'visible') shown = shown && inside(p.getBoundingClientRect());
  // The tree's visible area: the scroll box's inside, cut by the window and every clipping ancestor.
  const sb = document.querySelector('#d-dtree .dt-box'), sr = sb.getBoundingClientRect();
  const va = { top: Math.max(0, sr.top + sb.clientTop), left: sr.left + sb.clientLeft, bottom: Math.min(innerHeight, sr.top + sb.clientTop + sb.clientHeight), right: sr.left + sb.clientLeft + sb.clientWidth };
  for (let p = sb.parentElement; p; p = p.parentElement) if (getComputedStyle(p).overflowY !== 'visible') { const q = p.getBoundingClientRect(); va.top = Math.max(va.top, q.top); va.bottom = Math.min(va.bottom, q.bottom); }
  res.push({ inWindow: shown, inBox: inside(va),
    allText: z.scrollHeight <= z.clientHeight + 1, onBody: z.parentElement === document.body, count: document.querySelectorAll('.dt-zoom').length });
}
document.querySelector('#d-log').dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
res.push({ goneAfterLeaving: !document.querySelector('.dt-zoom') });
dtZoom(el); showTab('summary');
res.push({ goneAfterTab: !document.querySelector('.dt-zoom') });
showTab('conversation'); dtZoom(document.querySelector('#d-dtree .dt-node')); closeDetail();
res.push({ goneAfterClose: !document.querySelector('.dt-zoom') });
return res;
})()`);
await send('Emulation.setDeviceMetricsOverride', { width: 900, height: 900, deviceScaleFactor: 1, mobile: false });
out.open2 = await ev(`openDetail('t2'); showTab('conversation'); true`);
await sleep(300);
out.p3 = await ev(`(() => { const e = document.querySelector('#d-dtree'); return { exists: !!e, hidden: e ? e.hidden : null }; })()`);
await sleep(200);
// DT_SHOTS=dir: tree open, mid-fold and folded at 1280x800, Conversation tab, then back to open.
if (shots) {
  const fs = await import('node:fs');
  const shot = async (name) => { const r = await send('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(`${shots}/${name}.png`, Buffer.from(r.result.data, 'base64')); };
  await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });
  await ev(`openDetail('t1'); showTab('conversation'); true`);
  await sleep(600);
  await shot('dtree-open');
  await ev(`showTab('summary'); true`);
  await sleep(400);
  await shot('dtree-summary');
  await ev(`showTab('conversation'); true`);
  await sleep(300);
  await ev(`document.querySelector('#d-dtree .dt-head').click(); true`);
  await sleep(110);
  await shot('dtree-mid');
  await sleep(500);
  await shot('dtree-shut');
  await ev(`document.querySelector('#d-dtree .dt-head').click(); true`);
  await sleep(500);
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }] });
  await ev(`document.documentElement.dataset.theme = 'dark'; true`);
  await sleep(400);
  await shot('dtree-dark-open');
  await ev(`document.querySelector('#d-dtree .dt-head').click(); true`);
  await sleep(500);
  await shot('dtree-dark-shut');
  await ev(`document.querySelector('#d-dtree .dt-head').click(); delete document.documentElement.dataset.theme; true`);
  await sleep(400);
  out.shotsTaken = ['dtree-open', 'dtree-summary', 'dtree-mid', 'dtree-shut', 'dtree-dark-open', 'dtree-dark-shut'].map((n) => `${shots}/${n}.png`);
}
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
    n_count = len(n_opts)
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
    check(f"{tag} tree scrolls sideways inside its box (.dt-box overflow-x {m['overflowX']!r})", m["overflowX"] in ("auto", "scroll"))
    check(f"{tag} every edge is an SVG path", all(e["tag"] == "path" for e in m["edges"]) and len(m["edges"]) > 0)
    check(f"{tag} .dt-box has a solid border of 1px+ on all 4 sides ({m['border']})",
          m["border"] is not None and all(w >= 1 and s == "solid" for w, s in m["border"]))
    check(f"{tag} border colour is visible on the background ({m['borderColor']} on {m['bg']})",
          m["borderColor"] not in (None, "rgba(0, 0, 0, 0)", "transparent", m["bg"]))
    check(f"{tag} every box lies inside the tree's laid-out area (.dt-wrap)", m["outside"], 0)
    a = m["area"] or {}
    check(f"{tag} .dt-box scrolls over exactly that area plus padding ({a})",
          a and abs(a["scrollW"] - a["wrapW"]) <= 1 and abs(a["scrollH"] - a["wrapH"]) <= 1)
    h = m["head"] or {}
    check(f"{tag} head shows a chevron and 'Decisions' above the box", (h.get("svg"), h.get("label"), h.get("above")), (True, "Decisions", True))
    check(f"{tag} head shows the decision count {n_count}", h.get("count"), str(n_count))
    same = lambda a, b: a is not None and b is not None and all(abs(x - y) <= 2 for x, y in zip(a[:3], b[:3]))
    check(f"{tag} head has a filled background ({h.get('bg')} on panel {h.get('panelBg')})",
          bool(h.get("bg")) and h["bg"][3] > 0.5 and not same(h["bg"], h.get("panelBg")))
    check(f"{tag} head text is the accent colour, not muted ({h.get('color')} accent {h.get('accent')} muted {h.get('muted')})",
          same(h.get("color"), h.get("accent")) and not same(h.get("color"), h.get("muted")))
    check(f"{tag} head has a visible accent border ({h.get('borderW')}px {h.get('borderColor')})",
          (h.get("borderW") or 0) >= 1 and same(h.get("borderColor"), h.get("accent")) and h["borderColor"][3] >= 0.3)
    check(f"{tag} head text is bigger and bolder than before ({h.get('fontPx')}px, weight {h.get('weight')})",
          (h.get("fontPx") or 0) >= 12 and (h.get("weight") or 0) >= 600)
    check(f"{tag} chevron sits on the title's row inside the head button", h.get("svgInRow"))


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
        shots = os.environ.get("DT_SHOTS", "")
        if shots:
            os.makedirs(shots, exist_ok=True)
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
    check("page loaded, polled and has task t1", res["ready"])
    for e in res.get("errors", []):
        print("     ", e[:300])
    check(f"no console errors or exceptions ({len(res.get('errors', []))})", len(res.get("errors", [])), 0)
    if not res["ready"]:
        return 1
    check("panel opened on Conversation", res["open"])
    for k in ("p1", "sum", "sumBack", "sumFresh", "p2", "fold0", "foldMid", "foldEnd", "hoverShut", "foldRedraw", "foldReload", "unfoldMid", "unfoldEnd",
              "ts", "zoom", "p3"):
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

    for tag in ("sum", "sumFresh"):
        m = res[tag]
        check(f"{tag}: tree sits in the Summary tab", (m["inSum"], m["inConv"]), (True, False))
        check(f"{tag}: tree is the first thing in the Summary tab", m["firstInPane"])
        check(f"{tag}: tree shown in Summary", (m["hidden"], m["shown"]), (False, True))
        check(f"{tag}: same 3 query nodes in Summary", [n["q"] for n in m["nodes"]], ["Tree or list?", "Which side?", "Animate it?"])
        layout_checks(f"summary {tag}", m, [3, 2, 2])
    m = res["sumBack"]
    check("back on Conversation: tree is in Conversation, before the log", (m["inConv"], m["inSum"], m["beforeLog"], m["shown"]), (True, False, True, True))

    m = res["p2"]
    check("poll brought decision D into the log (setup)", any(flat("Decision open: Colour edges?") in flat(t) for t in m["logTexts"]))
    check("poll grew the tree without a reload", res["sameLoad"])
    check("4 nodes after the poll", [n["q"] for n in m["nodes"]], ["Tree or list?", "Which side?", "Animate it?", "Colour edges?"])
    check("one open node, the new one", [n["open"] for n in m["nodes"]], [False, False, False, True])
    check("open node's options are neither on nor off", [o["on"] or o["off"] for o in m["nodes"][-1]["opts"]] if m["nodes"] else None, [False, False])
    check(".dt-edge.on count unchanged by an open decision", sum(e["on"] for e in m["edges"]), 3)
    layout_checks("phase 2", m, [3, 2, 2, 2])
    check(f"tree is wider than its box at 900px (setup: {m['scrollW']} > {m['clientW']})", m["scrollW"] > m["clientW"])
    check(f"grown tree scrolled its box to show the newest decision (scrollLeft {m['scrollL']})",
          m["scrollL"] >= m["scrollW"] - m["clientW"] - 1)

    f0, fm, fe = res["fold0"], res["foldMid"], res["foldEnd"]
    full = f0["h"]
    check(f"tree starts open (shut, aria-expanded, height {full:.1f})", (f0["shut"], f0["aria"], full > 20), (False, "true", True))
    check("a hover zoom is up before the fold (setup)", res["zoomBeforeShut"], 1)
    check("clicking the head folds: shut class + aria-expanded false + title Expand", (fm["shut"], fm["aria"], fm["title"]), (True, "false", "Expand"))
    check("clicking the head drops the hover zoom", fm["zooms"], 0)
    check(f"fold animates: height at ~100ms is between 0 and full ({fm['h']:.1f} of {full:.1f})", 0.5 < fm["h"] < full - 0.5)
    check(f"after ~450ms the fold is closed (height {fe['h']:.1f}, visibility {fe['vis']})", (fe["h"] <= 0.5, fe["vis"]), (True, "hidden"))
    check("fold choice saved", fe["saved"], "1")
    check(f"folded: the count badge turns solid ({f0['countBg']} -> {fe['countBg']})",
          fe["countBg"] != f0["countBg"] and not fe["countBg"].startswith("color(") and fe["countBg"].startswith("rgb("))
    check(f"hovering where a box was while shut raises no zoom {res['hoverShut']}", res["hoverShut"], {"hitBox": False, "zooms": 0})
    r = res["foldRedraw"]
    check(f"a redraw keeps the tree folded (height {r['h']:.1f})", (r["shut"], r["aria"], r["h"] <= 0.5, r["vis"]), (True, "false", True, "hidden"))
    check("page reloaded and polled (setup)", res["reloadReady"])
    r = res["foldReload"]
    check(f"a reload keeps the tree folded (height {r['h']:.1f})", (r["shut"], r["aria"], r["h"] <= 0.5, r["vis"]), (True, "false", True, "hidden"))
    um, ue = res["unfoldMid"], res["unfoldEnd"]
    check(f"clicking again unfolds with an animation ({um['h']:.1f} of {full:.1f} at ~100ms)", (um["shut"], 0.5 < um["h"] < full - 0.5), (False, True))
    check(f"unfolded tree is back to full height ({ue['h']:.1f} vs {full:.1f})", abs(ue["h"] - full) <= 1)
    check("unfolded: aria-expanded true, title Collapse, visible, saved 0", (ue["aria"], ue["title"], ue["vis"], ue["saved"]),
          ("true", "Collapse", "visible", "0"))

    t = res["ts"]
    check("text size picker set --ts to 1.3 (setup)", t["ts"], "1.3")
    check("text size change redid the tree layout without a poll", t["relaid"])
    check(f"no two tree boxes overlap after a text size change ({t['n']} boxes)", t["overlaps"], [])

    z = res["zoom"]
    for tag, r in zip(("long node", "option"), z[:2]):
        check(f"hover zoom of {tag} is not cut by the window or the tree strip", r["inWindow"])
        check(f"hover zoom of {tag} shows all its text", r["allText"])
        check(f"hover zoom of {tag} is one copy on the page body", (r["onBody"], r["count"]), (True, 1))
    check("hover zoom of a short option stays over the tree's box", z[1]["inBox"])
    check("hover zoom goes away when the pointer leaves the tree", z[2]["goneAfterLeaving"])
    check("hover zoom goes away when another tab opens", z[3]["goneAfterTab"])
    check("hover zoom goes away when the panel closes", z[4]["goneAfterClose"])
    check("#d-dtree hidden for a log without decisions", res["p3"]["hidden"], True)
    for s in res.get("shotsTaken", []):
        check(f"screenshot saved {s}", os.path.getsize(s) > 1000 if os.path.exists(s) else False)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{passes} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
