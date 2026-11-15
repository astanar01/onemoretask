"""A Claude card can pick Codex workers as subagents: the Subagent model picker lists Claude, then Codex (when Codex is
installed); a Codex card keeps its Codex-only list; a Codex pick counts on a Claude card, a Claude pick does not on a
Codex card; a Codex worker row (cli "codex") shows a Codex tag and the model's name. Drives the real index.html +
i18n.js in headless Chrome over raw CDP, with /api faked by a stub server on a free port (never the live board).

Run: python3 tests/test_codex_subagents_ui.py  (stdlib + node 22+ + Google Chrome). SHOT_DIR=<dir> keeps the screenshots."""
import json
import os
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
TMP = tempfile.mkdtemp(prefix="codex-subagents-test-")
SHOTS = os.environ.get("SHOT_DIR") or TMP
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}

OLD = "2026-11-14T09:00:00+00:00"
NOW = time.time()
CODEX = {"installed": True}
MODELS = [{"id": "gpt-5.5", "name": "GPT-5.5"}, {"id": "gpt-5.6-sol", "name": "GPT-5.6-Sol"}]
CLAUDE_VERSIONS = {"fable": "claude-fable-5", "opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5"}


def card(tid, i, agent=None, **kw):
    t = {"id": tid, "title": f"Card {tid}", "notes": "", "priority": "", "tags": [], "column": "prog", "parent": None,
         "order": i, "created": OLD, "updated": OLD, "delegate": True, **kw}
    if agent:
        t["agent"] = {"id": tid, "started": OLD, **agent}
    return t


SUBS = [
    {"id": "a1", "name": "Find where the board saves", "type": "Explore", "started": NOW - 400, "updated": NOW - 20,
     "finished": False, "action": "Grep save_tasks", "skills": ["cmm-rules"], "tokens": 42000},
    {"id": "cx1", "name": "Write the parser tests", "type": "gpt-5.5", "cli": "codex", "started": NOW - 250,
     "updated": NOW - 5, "finished": False, "action": "Running pytest", "skills": [], "tokens": 18000},
]
TASKS = [
    card("cl", 0, {"model": "opus"}, subagentModel="gpt-5.5"),           # Claude session, Codex pick
    card("clc", 1, {"model": "opus"}, subagentModel="sonnet"),           # Claude session, Claude pick
    card("cx", 2, {"model": "gpt-5.5", "cli": "codex"}, subagentModel="opus"),          # Codex session, Claude pick
    card("cxx", 3, {"model": "gpt-5.5", "cli": "codex"}, subagentModel="gpt-5.6-sol"),  # Codex session, Codex pick
    card("new", 4, None, subagentModel="gpt-5.6-sol"),                   # not sent; session picker on default (Claude)
]
AGENTS = {t["id"]: {"id": t["id"], "state": "idle", "phase": "finished", "reason": "", "log": [], "subagents": []}
          for t in TASKS if "agent" in t}
AGENTS["cl"].update(phase="working", subagents=SUBS)
AGENTS["cx"]["cli"] = AGENTS["cxx"]["cli"] = "codex"
BOARD = {"version": 1, "columns": [{"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
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
            return self.send_json({"models": MODELS, "claude": True, "codex": CODEX["installed"], "claude_models": CLAUDE_VERSIONS})
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
        if self.path.startswith("/__nocodex"):
            CODEX["installed"] = False
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
const init = await send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('task-board:lang', 'en'); localStorage.setItem('task-board:theme', 'light'); } catch (e) {}` });
await send('Page.navigate', { url });
let ready = null;
for (let i = 0; i < 100 && ready !== true; i++) {
  await sleep(100);
  ready = await ev(`typeof polledOnce !== 'undefined' && polledOnce && state.tasks.length > 0 && Object.keys(agents).length > 0 && clis.codex === true`);
}
await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: init.result.identifier });
// The picker as groups of option values, its value, subModel and the Subagents row of Technical details.
const READ = `(() => { const sel = document.querySelector('#d-submodel');
  const kids = [...sel.children].map((c) => c.tagName === 'OPTGROUP' ? { group: c.label, opts: [...c.children].map((o) => o.value) } : c.value);
  const dt = [...document.querySelectorAll('#d-tech-list dt')].find((x) => x.textContent === 'Subagents');
  return { kids, value: sel.value, sub: subModel(task(openId)), info: dt ? dt.nextElementSibling.textContent : null,
           labels: [...sel.options].map((o) => o.textContent) }; })()`;
const results = { ready, on: {}, off: {} };
for (const c of ['cl', 'clc', 'cx', 'cxx', 'new']) { await ev(`openDetail('${c}')`); await sleep(60); results.on[c] = await ev(READ); }

// Subagent rows: the detail list and the card face.
await ev(`openDetail('cl')`); await sleep(60);
const ROWS = `[...document.querySelectorAll('#d-sa-list li')].map((li) => ({ id: li.dataset.id, name: li.querySelector('.nm').textContent,
  tag: li.querySelector('.cx-tag')?.textContent ?? null, model: li.querySelector('.cx-model')?.textContent ?? null }))`;
results.rows = await ev(ROWS);
results.face = await ev(`[...document.querySelectorAll('.card-subs li')].map((li) => ({ tag: li.querySelector('.cx-tag')?.textContent ?? null, title: li.title, name: li.querySelector('.nm').textContent }))`);

// Screenshots: (a) the picker's options on a Claude card (shown as a list box: a headless shot cannot show an open
// native menu), (b) the subagent list with one Claude row and one Codex row, plus the card face.
await ev(`openDetail('new')`); await sleep(80);
await ev(`var sm = document.querySelector('#d-submodel'); sm.size = sm.options.length + 2; sm.style.height = 'auto'; sm.scrollIntoView({ block: 'center' })`); await sleep(150);
results.shotA = await shot('codex-subagents-picker.png', '#d-submodel', 40);
await ev(`document.querySelector('#d-submodel').size = 0`);
await ev(`openDetail('cl')`); await sleep(80);
await ev(`var sa = document.querySelector('#d-subagents'); sa.open = true; document.querySelectorAll('#d-sa-list li').forEach((li) => li.click()); sa.scrollIntoView({ block: 'center' })`); await sleep(200);
results.shotB = await shot('codex-subagents-list.png', '#d-subagents');
await ev(`closeDetail()`); await sleep(150);
await ev(`document.querySelector('.card-subs').scrollIntoView({ block: 'center' })`); await sleep(100);
results.faceShot = await shot('codex-subagents-card.png', '.card-subs', 60);

// Codex not installed: a Claude card lists only Claude models, and its Codex pick stops counting.
await ev(`fetch('/__nocodex', { method: 'PUT' })`);
await ev(`await loadCodexModels()`);
results.clisOff = await ev(`clis.codex`);
for (const c of ['cl', 'clc', 'cxx']) { await ev(`openDetail('${c}')`); await sleep(60); results.off[c] = await ev(READ); }
results.errors = errors;
console.log(JSON.stringify(results));
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
    os.makedirs(SHOTS, exist_ok=True)
    try:
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome"), SHOTS],
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

    claude = ["fable", "opus", "sonnet", "haiku"]
    codex = ["gpt-5.5", "gpt-5.6-sol"]
    both = ["", {"group": "Claude", "opts": claude}, {"group": "Codex", "opts": codex}]
    on, off = res["on"], res["off"]
    check("no page errors", res["errors"], [])
    check("page loaded, polled, Codex installed", res["ready"])

    check("Claude card, Codex installed: Same as session, then a Claude group and a Codex group", on["cl"]["kids"], both)
    check("unsent card on a Claude model: both groups", on["new"]["kids"], both)
    check("Codex names read as people know them", on["new"]["labels"][-2:], ["GPT-5.5", "GPT-5.6-Sol"])
    check("Claude card: the Codex pick is kept and shown", (on["cl"]["value"], on["cl"]["sub"]), ("gpt-5.5", "gpt-5.5"))
    check("Claude card: the info row names the Codex pick", on["cl"]["info"], "allowed (GPT-5.5)")
    check("unsent card: the Codex pick is kept", on["new"]["value"], "gpt-5.6-sol")
    check("Claude card: a Claude pick still counts", (on["clc"]["value"], on["clc"]["sub"]), ("sonnet", "sonnet"))
    check("Codex card: Codex-only list, unchanged", on["cx"]["kids"], [""] + codex)
    check("Codex card: a Claude pick does not count", (on["cx"]["value"], on["cx"]["sub"], on["cx"]["info"]), ("", "", "allowed"))
    check("Codex card: a Codex pick counts", (on["cxx"]["value"], on["cxx"]["sub"]), ("gpt-5.6-sol", "gpt-5.6-sol"))

    check("Codex not installed (after reload of the list)", res["clisOff"], False)
    check("Claude card, no Codex: only the Claude models, no groups", off["cl"]["kids"], [""] + claude)
    check("Claude card, no Codex: the Codex pick stops counting", (off["cl"]["value"], off["cl"]["sub"], off["cl"]["info"]), ("", "", "allowed"))
    check("Claude card, no Codex: a Claude pick still counts", off["clc"]["sub"], "sonnet")
    check("Codex card, no Codex flag: list and pick unchanged", (off["cxx"]["kids"], off["cxx"]["sub"]), ([""] + codex, "gpt-5.6-sol"))

    rows = {r["id"]: r for r in res["rows"]}
    check("subagent list: two rows", sorted(rows), ["a1", "cx1"])
    check("Codex row: Codex tag and the model's name", (rows["cx1"]["tag"], rows["cx1"]["model"]), ("Codex", "GPT-5.5"))
    check("Claude row (no cli): no tag, no model", (rows["a1"]["tag"], rows["a1"]["model"]), (None, None))
    face = {r["name"]: r for r in res["face"]}
    check("card face: Codex row has the tag and the model name in its tooltip",
          (face["Write the parser tests"]["tag"], face["Write the parser tests"]["title"].startswith("GPT-5.5")), ("Codex", True))
    check("card face: Claude row has no tag", (face["Find where the board saves"]["tag"], face["Find where the board saves"]["title"].startswith("Explore")), (None, True))
    for k in ("shotA", "shotB", "faceShot"):
        check(f"{k}: element on screen", isinstance(res[k], dict) and res[k].get("width", 0) > 0 and res[k].get("height", 0) > 0)
    print("screenshots:", *(os.path.join(SHOTS, n) for n in ("codex-subagents-picker.png", "codex-subagents-list.png", "codex-subagents-card.png")))
    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
