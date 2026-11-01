"""The small "Used 5 MCP tools" line between chat messages: where rows land, what each row says, that an opened list
survives a redraw, and what is left of the old MCP lines. Drives the real index.html + i18n.js in headless Chrome over
raw CDP, with /api faked by a stub server on a free port (never the live board, never .task_board).

Run: python3 tests/test_mcp_uses_ui.py  (stdlib + node 22+ + Google Chrome). SHOT_DIR=<dir> keeps the screenshots."""
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
TMP = tempfile.mkdtemp(prefix="mcp-uses-test-")
SHOTS = os.environ.get("SHOT_DIR") or TMP
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}

COLS = [{"id": "prog", "name": "In Progress", "done": False, "role": "progress"},
        {"id": "done", "name": "Done", "done": True}]
OLD = "2026-10-15T09:00:00+00:00"


def iso(minute):
    h, m = divmod(minute, 60)
    return f"2026-10-15T{10 + h:02d}:{m:02d}:00+00:00"


def at(minute, sec=0):
    return datetime.fromisoformat(iso(minute)).astimezone(timezone.utc).timestamp() + sec


def msg(minute, text="A message"):
    return {"from": "claude", "status": "progress", "at": iso(minute), "message": text}


def you(minute, status="reply"):
    return {"from": "you", "status": status, "at": iso(minute), "message": "From you"}


def row(rid, minute, state="done", what="what it looked for", sub=False, server="muninn", tool="muninn_recall"):
    return {"id": rid, "server": server, "tool": tool, "what": what, "at": at(minute), "state": state, "sub": sub}


# card id: (phase, log, rows, extra agent fields)
CARDS = {
    "groups": ("finished", [msg(0), msg(10)],
               [row("g1", 1), row("g2", 2, what="a very long search text " * 4 + "x" * 200), row("g3", 3), row("g4", 11), row("g5", 12)], {}),
    "one": ("finished", [msg(0)], [row("o1", 1)], {}),
    "none": ("finished", [msg(0)], [], {}),
    "short": ("finished", [msg(10)], [row("s1", 1), row("s2", 2)], {}),
    "live": ("working", [msg(10)], [row("l1", 1, "open"), row("l2", 11, "open")], {}),
    "waits": ("needs_you", [msg(10)], [row("w1", 11, "open")], {"reason": "Asks something"}),
    # An unanswered use from a run that stopped, then a reply starts a new run that has used no tool yet.
    "again": ("working", [msg(0), you(10)], [row("a1", 5, "open")], {}),
    # The same use with no new run: a report or a note while it runs does not make it old.
    "report": ("working", [you(0), msg(10)], [row("r1", 5, "open")], {}),
    "noted": ("working", [you(0), you(10, "note")], [row("n1", 5, "open")], {}),
    "newrun": ("working", [msg(0), you(10)], [row("b1", 5, "open"), row("b2", 11, "open")], {}),
    "marks": ("finished", [msg(0)], [row("m1", 1, "failed"), row("m2", 2, sub=True)], {}),
    "evil": ("finished", [msg(0)], [row("e1", 1, what="<img src=x onerror=window.__xss=1>",
                                        server="<b>srv</b>", tool="<i>tool</i>")], {}),
    "oldmcp": ("working", [msg(0)], [], {"mcp": {"connecting": ["muninn"], "failed": [{"name": "wp-log", "error": "no node"}],
                                                 "calls": [{"server": "muninn", "tool": "recall", "since": at(1)}]}}),
    "oldfin": ("finished", [msg(0)], [], {"mcp": {"failed": [{"name": "wp-log", "error": "no node"}]}}),
    "long": ("finished", [msg(i) for i in range(0, 40)], [row("x1", 2), row("x2", 5), row("x3", 20, what="in a gap")], {}),
}
TASKS, AGENTS = [], {}
for i, (tid, (phase, log, rows, extra)) in enumerate(CARDS.items()):
    TASKS.append({"id": tid, "title": f"Card {tid}", "notes": "", "priority": "", "tags": [], "column": "prog",
                  "parent": None, "order": i, "created": OLD, "updated": OLD, "agent": {"id": tid, "started": OLD}})
    AGENTS[tid] = {"id": tid, "state": "idle", "phase": phase, "reason": "", "log": log, "mcp_uses": rows, "mcp": None, **extra}
BOARD = {"version": 1, "columns": COLS, "tasks": TASKS}


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
        if self.path.startswith("/__change"):   # the test's "the session did one more thing"
            AGENTS["groups"]["mcp_uses"] = AGENTS["groups"]["mcp_uses"] + [row("g6", 4, what="a new one")]
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
const ids = JSON.parse(process.env.CARD_IDS);
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
const sleep = (ms) => new Promise(res => setTimeout(res, ms));
await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1300, height: 900, deviceScaleFactor: 1, mobile: false });
const load = async (lang, theme) => {
  const init = await send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('task-board:lang', '${lang}'); localStorage.setItem('task-board:theme', '${theme}'); } catch (e) {}` });
  await send('Page.navigate', { url });
  let r = null;
  for (let i = 0; i < 100 && r !== true; i++) {
    await sleep(100);
    r = await ev(`typeof polledOnce !== 'undefined' && polledOnce && state.tasks.length > 0 && Object.keys(agents).length > 0`);
  }
  await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: init.result.identifier });
  return r === true;
};
// What the log holds, in order: a message, or a uses line with its summary and rows.
const READ = `[...document.querySelectorAll('#d-log > li')].map((li) => li.classList.contains('k-uses')
  ? { uses: li.querySelector('summary').textContent.trim(), spin: !!li.querySelector('summary .turning'), open: li.querySelector('details').open,
      noi: !li.hasAttribute('data-i'), rows: [...li.querySelectorAll('ul li')].map((x) => ({ text: x.textContent, failed: x.classList.contains('failed'),
      time: x.querySelector('time').textContent, imgs: x.querySelectorAll('img, b, i').length })) }
  : { cls: li.className, i: li.dataset.i ?? null, text: li.querySelector('.msg')?.textContent ?? '' })`;
const results = { cards: {} };
results.ready = await load('en', 'light');
for (const c of ids) {
  await ev(`openDetail(${JSON.stringify(c)})`);
  await sleep(60);
  results.cards[c] = await ev(READ);
}
results.xss = await ev(`window.__xss === undefined`);

// The pure function, with a pending entry on the page's clock.
results.groups = await ev(`(() => {
  const old = { at: '2026-10-15T10:00:00+00:00' }, now = Date.now() / 1000;
  const pend = { at: new Date(now * 1000).toISOString(), state: 'sending' };
  const r = (x) => ({ id: 'r' + x, at: x });
  const t0 = Date.parse(old.at) / 1000;
  const ids = (g) => g.map((s) => s.map((x) => x.at));
  return {
    pending: ids(mcpGroups([old, pend], [r(now + 60)], false)).map((s) => s.length),
    between: ids(mcpGroups([old, pend], [r(t0 + 1)], false)).map((s) => s.length),
    same: ids(mcpGroups([old], [r(t0)], false)).map((s) => s.length),
    keep: ids(mcpGroups([old], [r(t0 - 1)], false)).map((s) => s.length),
    cut: ids(mcpGroups([old], [r(t0 - 1)], true)).map((s) => s.length),
    empty: ids(mcpGroups([], [r(t0)], false)).map((s) => s.length),
  };
})()`);

// An opened list stays open over a real rebuild: the node must be new, or the check proves nothing.
await ev(`openDetail('groups'); showTab('conversation')`); await sleep(60);
await ev(`document.querySelector('#d-log li.k-uses summary').click()`); await sleep(120);
results.openedNow = await ev(`[...usesOpen]`);
await ev(`document.querySelector('#d-log li.k-uses details').__tag = 1`);
for (const [theme, name] of [['light', 'light'], ['dark', 'dark']]) {
  await ev(`document.documentElement.dataset.theme = '${theme}'`);
  await sleep(500);
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(`${outdir}/mcp-uses-${name}.png`, Buffer.from(shot.result.data, 'base64'));
}
await ev(`document.documentElement.dataset.theme = 'light'`);
await ev(`fetch('/__change', { method: 'PUT' })`);
let gone = false;
for (let i = 0; i < 30 && !gone; i++) {
  await ev(`await pollAgents()`); await sleep(100);
  gone = await ev(`document.querySelector('#d-log li.k-uses details').__tag === undefined`);
}
results.rebuilt = gone;
results.afterRebuild = await ev(READ);
await ev(`openDetail('one')`); await sleep(60);
await ev(`openDetail('groups')`); await sleep(60);
results.comeBack = await ev(READ);

// In French.
results.readyFr = await load('fr', 'light');
await ev(`openDetail('groups')`); await sleep(60);
results.fr = await ev(READ);
await ev(`openDetail('live')`); await sleep(60);
results.frLive = await ev(READ);
await ev(`openDetail('marks')`); await sleep(60);
results.frMarks = await ev(READ);
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
    env = dict(os.environ, CARD_IDS=json.dumps(list(CARDS)))
    try:
        p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(dport), os.path.join(TMP, "chrome"), SHOTS],
                           capture_output=True, text=True, timeout=120, env=env)
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
    check("page loaded and polled", res["ready"])
    c = res["cards"]
    uses = lambda items: [x for x in items if "uses" in x]
    shape = lambda items: ["uses" if "uses" in x else "msg" for x in items]

    check("groups: 3 before the second message, 2 after the last", shape(c["groups"]), ["msg", "uses", "msg", "uses"])
    g = uses(c["groups"])
    check("groups: summaries", [x["uses"] for x in g], ["Used 3 MCP tools", "Used 2 MCP tools"])
    check("groups: rows in each", [len(x["rows"]) for x in g], [3, 2])
    check("groups: closed at first", [x["open"] for x in g], [False, False])
    check("groups: no scroll anchor on the line", [x["noi"] for x in g], [True, True])
    r0 = g[0]["rows"][0]
    check("a row has the tool and what it looked for", "muninn · muninn_recall" in r0["text"] and "what it looked for" in r0["text"], True)
    check("a row has a time with seconds", r0["time"].count(":") == 2 and r0["time"] in r0["text"], True)
    check("one row: singular", [x["uses"] for x in uses(c["one"])], ["Used 1 MCP tool"])
    check("no rows: no line at all", uses(c["none"]), [])
    check("short log: rows before the first message show above it", shape(c["short"]), ["uses", "msg"])
    check("a pending message: a later row lands in the last slot", res["groups"]["pending"], [0, 0, 1])
    check("a row between a message and a pending one", res["groups"]["between"], [0, 1, 0])
    check("a row at a message's own time goes before it", res["groups"]["same"], [1, 0])
    check("a row before the first message: kept on a short log", res["groups"]["keep"], [1, 0])
    check("a row before the first message: dropped on a cut log", res["groups"]["cut"], [0, 0])
    check("no messages at all: the row lands in the only slot", res["groups"]["empty"], [1])

    live = uses(c["live"])
    check("live: the older open row has no spinner and reads no answer",
          (live[0]["spin"], live[0]["uses"], "no answer" in live[0]["rows"][0]["text"]), (False, "Used 1 MCP tool", True))
    check("live: the newest open row spins and counts",
          (live[1]["spin"], live[1]["uses"], "running" in live[1]["rows"][0]["text"]), (True, "Using MCP tools (1 so far)…", True))
    waits = uses(c["waits"])
    check("needs you: reads running, no spinner, says Used",
          (waits[0]["spin"], waits[0]["uses"], "running" in waits[0]["rows"][0]["text"]), (False, "Used 1 MCP tool", True))
    a = uses(c["again"])[0]
    check("a new run with no tool yet: the old open row does not spin and reads no answer",
          (a["spin"], a["uses"], "no answer" in a["rows"][0]["text"], "running" in a["rows"][0]["text"]),
          (False, "Used 1 MCP tool", True, False))
    for name in ("report", "noted"):
        x = uses(c[name])[0]
        check(f"same run, then {name}: the open row still spins",
              (x["spin"], x["uses"], "running" in x["rows"][0]["text"]), (True, "Using MCP tools (1 so far)…", True))
    n = uses(c["newrun"])
    check("a new run's own open row spins, the old one does not",
          [(x["spin"], "no answer" in x["rows"][0]["text"]) for x in n], [(False, True), (True, False)])
    m = uses(c["marks"])[0]["rows"]
    check("a failed row reads did not work", (m[0]["failed"], "did not work" in m[0]["text"]), (True, True))
    check("a done row has no mark", ("did not work" in m[1]["text"], m[1]["failed"]), (False, False))
    check("a subagent's row reads helper", "helper" in m[1]["text"] and "helper" not in m[0]["text"], True)
    e = uses(c["evil"])[0]["rows"][0]
    check("html in a row shows as text", "<img src=x onerror=window.__xss=1>" in e["text"] and "<b>srv</b>" in e["text"], True)
    check("html in a row makes no element", e["imgs"], 0)
    check("html in a row runs nothing", res["xss"])

    words = " ".join(x.get("text", "") for x in c["oldmcp"])
    check("working: no connecting line", "connecting" in words, False)
    check("working: no using line", "is using MCP" in words, False)
    check("working: Could not connect still shows", "Could not connect to MCP: wp-log" in words, True)
    check("working: with its error", "no node" in words, True)
    check("finished: Could not connect does not show", "Could not connect" in " ".join(x.get("text", "") for x in c["oldfin"]), False)

    check("long: the first shown message is number 10", c["long"][0].get("i"), "10")
    lng = uses(c["long"])
    check("long: rows older than the oldest shown message do not show",
          [[("x1" if "x1" in r["text"] else "") for r in x["rows"]] for x in lng if "Used 2" in x["uses"]], [])
    check("long: only the row in a gap shows", [(x["uses"], x["rows"][0]["text"].count("in a gap")) for x in lng], [("Used 1 MCP tool", 1)])

    check("clicking opens the list and is remembered", res["openedNow"], ["g1"])
    check("the node really was rebuilt", res["rebuilt"])
    after = uses(res["afterRebuild"])
    check("an opened list stays open over a rebuild", after[0]["open"])
    check("... and shows the new row", (after[0]["uses"], len(after[0]["rows"])), ("Used 4 MCP tools", 4))
    check("... and the other list stayed closed", after[1]["open"], False)
    check("another card and back: closed", [x["open"] for x in uses(res["comeBack"])], [False, False])

    check("French: page loaded", res["readyFr"])
    check("French: summaries", [x["uses"] for x in uses(res["fr"])], ["4 outils MCP utilisés", "2 outils MCP utilisés"])
    check("French: one row", uses(res["frLive"])[0]["uses"], "1 outil MCP utilisé")
    check("French: live", uses(res["frLive"])[1]["uses"], "Utilise des outils MCP (1 pour l'instant)…")
    check("French: no answer", "pas de réponse" in uses(res["frLive"])[0]["rows"][0]["text"], True)
    fm = uses(res["frMarks"])[0]["rows"]
    check("French: did not work, helper", ("n'a pas marché" in fm[0]["text"], "sous-agent" in fm[1]["text"]), (True, True))
    print("screenshots:", os.path.join(SHOTS, "mcp-uses-light.png"), os.path.join(SHOTS, "mcp-uses-dark.png"))
    print(f"\n{passes} passed, {fails} failed  (temp dir {TMP})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
