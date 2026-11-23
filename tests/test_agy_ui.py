"""Antigravity on the page: the model pickers gain an Antigravity group (S8) and stay as they were without it (S9);
an Antigravity pick sends to Antigravity (S10); model names never show "agy:" (S11); the card names its CLI (S35,
S36, S38); the subagent picker (S39, S44), the answer-only default (S48), the observation picker (S49), the context
bar (S31), the subagent tag (S32) and the French strings (S53). Drives the real index.html + i18n.js in headless
Chrome over raw CDP, with /api faked by a stub server on a free port (never the live board, never the real agy).

Run: python3 tests/test_agy_ui.py  (stdlib + node 22+ + Google Chrome). SHOT_DIR=<dir> keeps the screenshots.
     PRINT_BASELINE=1 python3 tests/test_agy_ui.py  prints the S9 option lists the page shows now."""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="agy-ui-test-")
SHOTS = os.environ.get("SHOT_DIR") or TMP
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}

OLD = "2026-11-14T09:00:00+00:00"
NOW = time.time()
CODEX_MODELS = [{"id": "gpt-5.5", "name": "GPT-5.5"}, {"id": "gpt-5.6-sol", "name": "GPT-5.6-Sol"}]
AG_MODELS = [{"id": "agy:gemini-3-pro", "name": "Gemini 3 Pro"}, {"id": "agy:gemini-3-flash", "name": "Gemini 3 Flash"}]
CLAUDE_VERSIONS = {"fable": "claude-fable-5", "opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5"}
# What /api/codex-models adds, per mode the driver switches to (PUT /__ag/<mode>). "absent" is an older server.
AG_MODES = {
    "absent": {},
    "off": {"antigravity": False, "antigravity_models": [], "antigravity_refreshing": False, "antigravity_error": ""},
    "on": {"antigravity": True, "antigravity_models": AG_MODELS, "antigravity_refreshing": False, "antigravity_error": ""},
    "login": {"antigravity": True, "antigravity_models": [], "antigravity_refreshing": False,
              "antigravity_error": "Antigravity is not logged in"},
    "other": {"antigravity": True, "antigravity_models": [], "antigravity_refreshing": False,
              "antigravity_error": "error: something else went wrong"},
}
STUB = {"ag": "on", "codex": True}

# S9 baseline: the option lists the page showed BEFORE Antigravity was added (read with PRINT_BASELINE=1 on commit
# c62e16e, Codex installed, the API answer without any antigravity key). Entries are values; a group is [label, values].
_CL = ["", "fable", "opus", "sonnet", "haiku", ["OpenAI (Codex)", ["gpt-5.5", "gpt-5.6-sol"]]]
_SUB2 = ["", ["Claude", ["fable", "opus", "sonnet", "haiku"]], ["Codex", ["gpt-5.5", "gpt-5.6-sol"]]]
BASELINE = {"cl": {"model": _CL, "review": _CL, "sub": _SUB2},
            "cx": {"model": _CL, "review": _CL, "sub": ["", "gpt-5.5", "gpt-5.6-sol"]},
            "new": {"model": _CL, "review": _CL, "sub": _SUB2}}


def card(tid, i, agent=None, column="prog", **kw):
    t = {"id": tid, "title": f"Card {tid}", "notes": "", "priority": "", "tags": [], "column": column, "parent": None,
         "order": i, "created": OLD, "updated": OLD, "delegate": True, **kw}
    if agent:
        t["agent"] = {"id": tid, "started": OLD, **agent}
    return t


AG_SUBS = [  # an Antigravity session's own subagents (server: agy.subagents)
    {"id": "conv-sub-1", "name": "researcher", "type": "—", "cli": "antigravity", "started": NOW - 100, "updated": NOW - 10,
     "finished": True, "action": "", "skills": [], "tokens": 0},
]
CL_SUBS = [  # a Claude session with one Claude subagent, one Codex worker and one Antigravity worker
    {"id": "a1", "name": "Find where the board saves", "type": "Explore", "started": NOW - 400, "updated": NOW - 20,
     "finished": False, "action": "Grep save_tasks", "skills": [], "tokens": 42000},
    {"id": "cx1", "name": "Write the parser tests", "type": "gpt-5.5", "cli": "codex", "started": NOW - 250,
     "updated": NOW - 5, "finished": False, "action": "Running pytest", "skills": [], "tokens": 18000},
    {"id": "ag1", "name": "Check the docs", "type": "agy:gemini-3-flash", "cli": "antigravity", "started": NOW - 200,
     "updated": NOW - 5, "finished": False, "action": "", "skills": [], "tokens": 900},
    {"id": "ag2", "name": "Odd model", "type": "agy:gemini-9-ultra", "cli": "antigravity", "started": NOW - 200,
     "updated": NOW - 5, "finished": True, "action": "", "skills": [], "tokens": 0},
]
REVIEW_LOG = [{"from": "you", "status": "review", "message": "Review it", "at": OLD},
              {"from": "claude", "status": "done", "message": "Two findings", "at": OLD}]
TASKS = [
    card("cl", 0, {"model": "opus"}, subagentModel="agy:gemini-3-flash"),                       # Claude, AG pick
    card("cx", 1, {"model": "gpt-5.5", "cli": "codex"}, subagentModel="agy:gemini-3-flash"),    # Codex, AG pick
    card("ag", 2, {"model": "agy:gemini-3-pro", "cli": "antigravity"}, subagentModel="agy:gemini-3-flash"),
    card("agc", 3, {"model": "agy:gemini-3-pro", "cli": "antigravity"}, subagentModel="gpt-5.5"),  # AG, Codex pick
    card("agx", 4, {"model": "agy:gemini-9-ultra", "cli": "antigravity"}),                      # label missing
    card("new", 5, None),
    card("ans", 6, None, answerOnly=True),
    card("hbA", 7, {"model": "agy:gemini-3-pro", "cli": "antigravity"}, column="rev"),           # AG worked, Claude reviewed
    card("hbC", 8, {"model": "opus"}, column="rev"),                                            # Claude worked, AG reviewed
]
AGENTS = {t["id"]: {"id": t["id"], "sessionId": t["id"], "state": "idle", "phase": "finished", "reason": "", "log": [],
                    "subagents": []} for t in TASKS if "agent" in t}
AGENTS["cl"].update(phase="working", subagents=CL_SUBS, context={"tokens": 50000, "peak": 60000, "max": 200000, "model": "claude-opus-5-5"})
AGENTS["cx"]["cli"] = "codex"
for k in ("ag", "agc", "agx"):
    AGENTS[k].update(cli="antigravity", context=None)
AGENTS["ag"]["subagents"] = AG_SUBS
AGENTS["hbA"].update(cli="claude", handback="Antigravity", log=REVIEW_LOG)
AGENTS["hbC"].update(cli="antigravity", handback="Claude", log=REVIEW_LOG)
BOARD = {"version": 1, "columns": [{"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
                                   {"id": "rev", "name": "Review", "done": False, "role": "review"},
                                   {"id": "done", "name": "Done", "done": True}], "tasks": TASKS}


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
            return self.send_json(AGENTS)
        if p == "/api/codex-models":
            return self.send_json({"models": CODEX_MODELS, "claude": True, "codex": STUB["codex"],
                                   "claude_models": CLAUDE_VERSIONS, **AG_MODES[STUB["ag"]]})
        if p == "/api/app-copy":
            return self.send_json({"current": ROOT, "copies": [{"path": ROOT, "main": True}]})
        if p == "/api/review-schedule":   # an older server: the schedule row stays hidden
            return self.send_json({}, 404)
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
        m = re.match(r"^/__ag/(\w+)(?:/(\w+))?", self.path)
        if m:
            STUB["ag"] = m.group(1)
            STUB["codex"] = m.group(2) != "nocodex"
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
const [url, chrome, dport, profile, outdir] = process.argv.slice(2);
const proc = spawn(chrome, ['--headless=new', `--remote-debugging-port=${dport}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', 'about:blank'], { stdio: 'ignore' });
const quit = (code) => { try { proc.kill(); } catch {} setTimeout(() => process.exit(code), 300); };
process.on('SIGTERM', () => quit(3)); process.on('SIGINT', () => quit(3));
setTimeout(() => { console.log(JSON.stringify({ error: 'HUNG driver' })); quit(2); }, 150000);
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
const ev = async (expression) => { const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, replMode: true }); return r.result.exceptionDetails ? { exc: r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text } : r.result.result.value; };
const sleep = (ms) => new Promise(res => setTimeout(res, ms));
const shot = async (name, sel, pad = 12) => {
  const r = await ev(`(() => { const b = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; })()`);
  const clip = { x: Math.max(0, r.x - pad), y: Math.max(0, r.y - pad), width: r.width + 2 * pad, height: r.height + 2 * pad, scale: 1 };
  const s = await send('Page.captureScreenshot', { format: 'png', clip, captureBeyondViewport: true });
  writeFileSync(`${outdir}/${name}`, Buffer.from(s.result.data, 'base64'));
  return r;
};
await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1300, height: 1000, deviceScaleFactor: 2, mobile: false });
await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });
const init = await send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('task-board:lang', 'en'); localStorage.setItem('task-board:theme', 'light'); localStorage.removeItem('taskboard.reviewModel'); localStorage.removeItem('taskboard.model'); } catch (e) {}` });
await fetch(url + '__ag/absent', { method: 'PUT' });
await send('Page.navigate', { url });
let ready = null;
for (let i = 0; i < 100 && ready !== true; i++) {
  await sleep(100);
  ready = await ev(`typeof polledOnce !== 'undefined' && polledOnce && state.tasks.length > 0 && Object.keys(agents).length > 0 && clis.codex === true`);
}
await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: init.result.identifier });
const R = {};
R.ready = ready;
// replMode hands back a promise the expression returns without waiting for it: each wait is an explicit await.
const mode = async (m) => { await ev(`await fetch('/__ag/${m}', { method: 'PUT' }); 0`); return ev(`await loadCodexModels(); 0`); };
// A select as a list: option values, and [label, values] for a group.
const LIST = (sel) => `[...document.querySelector('${sel}').children].map((c) => c.tagName === 'OPTGROUP' ? [c.label, [...c.children].map((o) => o.value)] : c.value)`;
const PICKERS = `({ model: ${LIST('#d-model')}, review: ${LIST('#d-review-model')}, sub: ${LIST('#d-submodel')} })`;
const atCard = async (c) => { await ev(`openDetail('${c}')`); await sleep(60); };

// S9: no antigravity key (older server), then antigravity false.
for (const m of ['absent', 'off']) {
  await mode(m);
  R['s9_' + m] = {};
  for (const c of ['cl', 'cx', 'new']) { await atCard(c); R['s9_' + m][c] = await ev(PICKERS); }
  R['s9_' + m].agGroups = await ev(`document.querySelectorAll('optgroup.ag-models').length`);
}
if (process.env.PRINT_BASELINE) { console.log(JSON.stringify({ baseline: R.s9_absent })); quit(0); }

// S8: three answers with Antigravity installed.
const GROUP = (sel) => `(() => { const g = document.querySelector('${sel} optgroup.ag-models'); if (!g) return null;
  const kids = [...g.parentElement.children]; return { label: g.label, at: kids.indexOf(g), codexAt: kids.findIndex((x) => x.classList && x.classList.contains('codex-models')),
  opts: [...g.children].map((o) => ({ v: o.value, text: o.textContent, dis: o.disabled })) }; })()`;
for (const m of ['on', 'login', 'other']) {
  await mode(m); await atCard('new');
  R['s8_' + m] = { model: await ev(GROUP('#d-model')), review: await ev(GROUP('#d-review-model')) };
}
await mode('off'); await atCard('new');
R.s8_off = { model: await ev(GROUP('#d-model')), review: await ev(GROUP('#d-review-model')) };
await mode('on');

// S10: picking an Antigravity model on an unsent card.
await atCard('new');
await ev(`(() => { const s = document.querySelector('#d-model'); s.value = 'agy:gemini-3-pro'; s.dispatchEvent(new Event('change')); })()`);
R.s10 = await ev(`({ text: document.querySelector('#d-send').textContent, title: document.querySelector('#d-send').title,
  shown: document.querySelector('#d-model').selectedOptions[0]?.textContent ?? null })`);
R.shotSend = await shot('agy-send.png', '#d-send-row', 20);
await ev(`(() => { const s = document.querySelector('#d-model'); s.value = ''; s.dispatchEvent(new Event('change')); })()`);

// S11: model names on the card pill, Technical details, Subagents rows, review picker.
const PILL = `document.querySelector('#d-agent .pill')?.textContent.trim() ?? null`;
const TECH = `(() => { const dt = [...document.querySelectorAll('#d-tech-list dt')].find((x) => x.textContent === 'Model'); return dt ? dt.nextElementSibling.textContent : null; })()`;
await atCard('ag'); R.s11_ag = { pill: await ev(PILL), tech: await ev(TECH) };
await atCard('agx'); R.s11_agx = { pill: await ev(PILL), tech: await ev(TECH) };
R.s11_fn = await ev(`[modelName('agy:gemini-3-pro'), modelName('agy:gemini-9-ultra'), prettyModel('agy:gemini-9-ultra')]`);
await ev(`localStorage.setItem('taskboard.reviewModel', 'agy:gemini-3-pro')`);
await atCard('ag');
R.s11_review = await ev(`({ value: document.querySelector('#d-review-model').value, text: document.querySelector('#d-review-model').selectedOptions[0]?.textContent ?? null })`);
await ev(`localStorage.removeItem('taskboard.reviewModel')`);
const ROWS = `[...document.querySelectorAll('#d-sa-list li')].map((li) => ({ id: li.dataset.id, text: li.textContent,
  cxTag: li.querySelector('.cx-tag')?.textContent ?? null, cxModel: li.querySelector('.cx-model')?.textContent ?? null,
  agTag: li.querySelector('.ag-tag')?.textContent ?? null, agModel: li.querySelector('.ag-model')?.textContent ?? null }))`;
await atCard('cl'); R.rowsCl = await ev(ROWS);
R.faceCl = await ev(`[...document.querySelectorAll('.card[data-id="cl"] .card-subs li, .card-subs li')].map((li) => ({ title: li.title, ag: li.querySelector('.ag-tag')?.textContent ?? null, cx: li.querySelector('.cx-tag')?.textContent ?? null, name: li.querySelector('.nm')?.textContent }))`);
await ev(`var sa = document.querySelector('#d-subagents'); sa.open = true; sa.scrollIntoView({ block: 'center' })`); await sleep(150);
R.shotSubs = await shot('agy-subagents.png', '#d-subagents');

// S31 + S32: the Antigravity card: no context bar, an Antigravity tag on its subagent row.
await atCard('ag');
R.s31 = await ev(`({ panelHidden: document.querySelector('#d-context').hidden, chip: !!document.querySelector('.card[data-id="ag"] .ctx'),
  clChip: !!document.querySelector('[data-id="cl"] .ctx') })`);
R.rowsAg = await ev(ROWS);

// S35: card pill, reply placeholder, "{who} reviewed them"; S36: attach menu and row; S38: hand-back button.
R.s35 = { pill: await ev(PILL), placeholder: await ev(`document.querySelector('#d-reply').placeholder`) };
const ATTACH = `({ menu: !document.querySelector('#d-copy-attach').hidden, tech: document.querySelector('#d-tech-list').textContent.includes('claude attach') })`;
R.s36 = {};
for (const c of ['cl', 'cx', 'ag']) { await atCard(c); R.s36[c] = await ev(ATTACH); }
const G = `{ available: true, changed: ['a.py'], ahead: [], target: 'main', branch: 'main' }`;
const NEXT = (c) => `(() => { const n = nextAction(task('${c}'), agents['${c}'], ${G}); return { title: n.title, text: n.text, labels: n.actions.map((a) => a.label) }; })()`;
R.s38 = { hbA: await ev(NEXT('hbA')), hbC: await ev(NEXT('hbC')) };

// S39 / S44: the subagent picker and which pick counts.
const SUB = `({ kids: ${LIST('#d-submodel')}, value: document.querySelector('#d-submodel').value, sub: subModel(task(openId)) })`;
R.sub = {};
for (const c of ['cl', 'cx', 'ag', 'agc', 'new']) { await atCard(c); R.sub[c] = await ev(SUB); }
await mode('on/nocodex');
for (const c of ['cl']) { await atCard(c); R.sub[c + '_nocodex'] = await ev(SUB); }
await mode('off');
for (const c of ['cl', 'ag']) { await atCard(c); R.sub[c + '_off'] = await ev(SUB); }
await mode('on');

// S48: the answer-only default model.
R.s48 = await ev(`(() => { const keep = clis, out = [];
  try {
    out.push(answerModel());
    clis = { ...keep, claude: false }; codexList = [{ id: 'gpt-5.5-luna', name: 'Luna' }]; out.push(answerModel());
    clis = { ...keep, claude: false, codex: false }; out.push(answerModel());
    agList = [{ id: 'agy:gemini-3-pro', name: 'Pro' }, { id: 'agy:gemini-3-ultra', name: 'Ultra' }]; out.push(answerModel());
  } finally { clis = keep; }
  return out; })()`);
await mode('on');
await atCard('ans');
R.s48_ans = await ev(`document.querySelector('#d-model').value`);

// S49: the observation picker leaves out Codex and Antigravity models.
await ev(`closeDetail()`); await sleep(80);
await ev(`openObs()`); await sleep(100);
R.s49 = await ev(`({ src: document.querySelectorAll('#d-model optgroup.ag-models').length, ag: document.querySelectorAll('#obs-model optgroup.ag-models').length, cx: document.querySelectorAll('#obs-model optgroup.codex-models').length,
  agOpts: [...document.querySelectorAll('#obs-model option')].filter((o) => o.value.startsWith('agy:')).length,
  opts: document.querySelectorAll('#obs-model option').length })`);
R.errors = errors;
console.log(JSON.stringify(R));
quit(0);
"""

fails = passes = 0


def check(name, got, want=True):
    global fails, passes
    ok = got == want
    fails += not ok
    passes += ok
    print(("ok  " if ok else "FAIL") + f" {name}" + ("" if ok else f": got {got!r}, want {want!r}"))


def i18n_fr_keys():
    src = open(os.path.join(ROOT, "i18n.js"), encoding="utf-8").read()
    m = re.search(r"/\* fr-start \*/([\s\S]*?)/\* fr-end \*/", src)
    return json.loads(m.group(1)) if m else {}


def main():
    port, dport = free_port(), free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    driver = os.path.join(TMP, "driver.mjs")
    open(driver, "w").write(DRIVER)
    os.makedirs(SHOTS, exist_ok=True)
    try:
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome"), SHOTS],
                           capture_output=True, text=True, timeout=180)
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
    if os.environ.get("PRINT_BASELINE"):
        print(json.dumps(res["baseline"]))
        return 0

    check("no page errors", res["errors"], [])
    check("page loaded, polled, Codex installed", res["ready"])

    # S9
    for m in ("absent", "off"):
        got = res["s9_" + m]
        check(f"S9 [{m}] no Antigravity group in any picker", got["agGroups"], 0)
        for c in ("cl", "cx", "new"):
            check(f"S9 [{m}] card {c}: #d-model, #d-review-model, #d-submodel exactly as before", got[c], BASELINE[c])

    # S8
    ids = [m["id"] for m in AG_MODELS]
    for sel in ("model", "review"):
        g = res["s8_on"][sel]
        check(f"S8 [on] {sel} picker: an Antigravity group with the models", g and (g["label"], [o["v"] for o in g["opts"]]), ("Antigravity", ids))
        check(f"S8 [on] {sel} picker: the group comes after the Codex group", bool(g) and g["at"] > g["codexAt"] >= 0)
        check(f"S8 [on] {sel} picker: options read as their labels", g and [o["text"] for o in g["opts"]], ["Gemini 3 Pro", "Gemini 3 Flash"])
        check(f"S8 [off] {sel} picker: no Antigravity group", res["s8_off"][sel], None)
        g = res["s8_login"][sel]
        check(f"S8 [logged out] {sel} picker: one disabled option asking to log in",
              g and [(o["text"], o["dis"]) for o in g["opts"]], [("Antigravity: log in once in a terminal", True)])
        g = res["s8_other"][sel]
        check(f"S8 [other error] {sel} picker: one disabled 'no models yet' option",
              g and [(o["text"], o["dis"]) for o in g["opts"]], [("Antigravity: no models yet", True)])

    # S10
    check("S10 an Antigravity pick: button reads Send to Antigravity", res["s10"]["text"], "Send to Antigravity")
    check("S10 an Antigravity pick: button title", res["s10"]["title"], "Start a background Antigravity session on this task")

    # S11
    check("S11 send picker shows the label", res["s10"]["shown"], "Gemini 3 Pro")
    check("S11 modelName/prettyModel: label, else slug, never agy:", res["s11_fn"], ["Gemini 3 Pro", "gemini-9-ultra", "gemini-9-ultra"])
    check("S11 card pill: Antigravity and the label", res["s11_ag"]["pill"], "Antigravity · Gemini 3 Pro")
    check("S11 Technical details Model: the label", res["s11_ag"]["tech"], "Gemini 3 Pro")
    check("S11 label missing: card pill shows the slug", res["s11_agx"]["pill"], "Antigravity · gemini-9-ultra")
    check("S11 label missing: Technical details shows the slug", res["s11_agx"]["tech"], "gemini-9-ultra")
    check("S11 review picker: an AG pick shows its label", res["s11_review"], {"value": "agy:gemini-3-pro", "text": "Gemini 3 Pro"})
    rows = {r["id"]: r for r in res["rowsCl"]}
    check("S11/S32 Claude card: Antigravity worker row has the Antigravity tag and the label",
          (rows.get("ag1", {}).get("agTag"), rows.get("ag1", {}).get("agModel")), ("Antigravity", "Gemini 3 Flash"))
    check("S11 Antigravity worker row, label missing: the slug", rows.get("ag2", {}).get("agModel"), "gemini-9-ultra")
    check("S11 no subagent row shows agy:", [r["id"] for r in res["rowsCl"] + res["rowsAg"] if "agy:" in r["text"]], [])
    check("Codex row keeps its Codex tag and model", (rows.get("cx1", {}).get("cxTag"), rows.get("cx1", {}).get("cxModel"), rows.get("cx1", {}).get("agTag")), ("Codex", "GPT-5.5", None))
    check("Claude row: no tag", (rows.get("a1", {}).get("cxTag"), rows.get("a1", {}).get("agTag")), (None, None))
    face = {r["name"]: r for r in res["faceCl"]}
    check("card face: Antigravity worker row tagged, its tooltip names the label, not agy:",
          (face.get("Check the docs", {}).get("ag"), face.get("Check the docs", {}).get("title", "").startswith("Gemini 3 Flash")), ("Antigravity", True))

    # S31, S32
    check("S31 context null: no context panel, no context chip on the card", (res["s31"]["panelHidden"], res["s31"]["chip"]), (True, False))
    check("S31 control: the Claude card with a context does show its chip", res["s31"]["clChip"], True)
    agrows = res["rowsAg"]
    check("S32 Antigravity session's subagent row: tag Antigravity (.ag-tag), no Codex tag",
          [(r["agTag"], r["cxTag"]) for r in agrows], [("Antigravity", None)])

    # S35
    check("S35 card pill names Antigravity", (res["s35"]["pill"] or "").startswith("Antigravity"), True)
    check("S35 reply box names Antigravity", res["s35"]["placeholder"], "Answer Antigravity — Cmd+Enter to send, paste pictures or files to attach")
    check("S35 '{who} reviewed them' names the reviewer's CLI", ", Antigravity reviewed them" in (res["s38"]["hbC"]["text"] or ""), True)

    # S36
    check("S36 Claude card: attach menu item and row shown", res["s36"]["cl"], {"menu": True, "tech": True})
    check("S36 Codex card: attach menu item and row hidden", res["s36"]["cx"], {"menu": False, "tech": False})
    check("S36 Antigravity card: attach menu item and row hidden", res["s36"]["ag"], {"menu": False, "tech": False})

    # S38
    check("S38 Antigravity worker, Claude reviewer: Send findings to Antigravity", "Send findings to Antigravity" in res["s38"]["hbA"]["labels"], True)
    check("S38 Claude worker, Antigravity reviewer: Send findings to Claude", "Send findings to Claude" in res["s38"]["hbC"]["labels"], True)

    # S39, S44
    claude = ["fable", "opus", "sonnet", "haiku"]
    codex = [m["id"] for m in CODEX_MODELS]
    sub = res["sub"]
    check("S39 Claude card: groups Claude, Codex, Antigravity in that order", sub["cl"]["kids"], ["", ["Claude", claude], ["Codex", codex], ["Antigravity", ids]])
    check("S39 unsent card on a Claude model: the three groups", sub["new"]["kids"], ["", ["Claude", claude], ["Codex", codex], ["Antigravity", ids]])
    check("S39 Claude card: an Antigravity pick counts", (sub["cl"]["value"], sub["cl"]["sub"]), ("agy:gemini-3-flash", "agy:gemini-3-flash"))
    check("S39 Claude card, no Codex: groups Claude, Antigravity", sub["cl_nocodex"]["kids"], ["", ["Claude", claude], ["Antigravity", ids]])
    check("S39 Codex card: Codex only; an Antigravity pick does not count", (sub["cx"]["kids"], sub["cx"]["sub"]), ([""] + codex, ""))
    check("S39 Antigravity off: Claude card has no Antigravity group and the AG pick stops counting",
          (sub["cl_off"]["kids"], sub["cl_off"]["sub"]), (["", ["Claude", claude], ["Codex", codex]], ""))
    check("S44 Antigravity card: only Antigravity models", sub["ag"]["kids"], [""] + ids)
    check("S44 Antigravity card: an Antigravity pick counts", (sub["ag"]["value"], sub["ag"]["sub"]), ("agy:gemini-3-flash", "agy:gemini-3-flash"))
    check("S44 Antigravity card: a Codex pick does not count", (sub["agc"]["value"], sub["agc"]["sub"]), ("", ""))

    # S48
    check("S48 answer-only default: Haiku, else Codex Luna, else first AG flash, else first AG",
          res["s48"], ["haiku", "gpt-5.5-luna", "agy:gemini-3-flash", "agy:gemini-3-pro"])
    check("S48 answer-only card opens on Haiku with Claude installed", res["s48_ans"], "haiku")

    # S49
    check("S49 precondition: the session picker it copies has the Antigravity group", res["s49"]["src"], 1)
    check("S49 observation picker: no Antigravity or Codex group, no AG option",
          (res["s49"]["ag"], res["s49"]["cx"], res["s49"]["agOpts"]), (0, 0, 0))
    check("S49 observation picker still lists the Claude models", res["s49"]["opts"] >= 5, True)

    # S53
    fr = i18n_fr_keys()
    for k in ("Send to Antigravity", "Start a background {who} session on this task", "Antigravity: log in once in a terminal",
              "Antigravity: no models yet",
              "The agent first plans how to split the work, then hands the parts to subagents (Claude's Agent tool, or Codex's or Antigravity's workers)"):
        check(f"S53 French line for {k!r}", bool(fr.get(k)), True)
    check("S53 the old delegate tooltip line is gone",
          "The agent first plans how to split the work, then hands the parts to subagents (Claude's Agent tool, or Codex's workers)" in fr, False)

    for k in ("shotSend", "shotSubs"):
        check(f"{k}: element on screen", isinstance(res.get(k), dict) and res[k].get("width", 0) > 0 and res[k].get("height", 0) > 0)
    print("screenshots:", *(os.path.join(SHOTS, n) for n in ("agy-send.png", "agy-subagents.png")))
    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
