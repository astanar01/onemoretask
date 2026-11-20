"""The MCP button in the top bar: it opens a list of every MCP server with its state in plain words and its number
of tools, counts the ones that do not work on a badge, fills in while the server still checks, and "Check again"
asks for a fresh check. A board server that runs older code (no /api/mcp) keeps the button hidden.
Drives the real index.html + i18n.js in headless Chrome over raw CDP, with /api faked by a stub server on a free port
(never the live board, never .task_board).

Run: python3 tests/test_mcp_menu_ui.py  (stdlib + node 22+ + Google Chrome). SHOT=<file.png> keeps the screenshot."""
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
TMP = tempfile.mkdtemp(prefix="mcp-menu-ui-test-")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
SHOT = os.environ.get("SHOT") or os.path.join(TMP, "mcp-menu.png")
SERVED = {"/": "index.html", "/index.html": "index.html", "/i18n.js": "i18n.js"}
COLS = [{"id": "todo", "name": "To Do", "done": False}, {"id": "done", "name": "Done", "done": True}]
BOARD = {"version": 1, "columns": COLS, "tasks": []}
AT = 1795200000


def row(name, status, tools=0, error="", label=None, src="user"):
    return {"name": name, "label": label or name, "status": status, "tools": tools, "error": error, "from": src}


BROKEN = row("broken-tool", "failed", 0, "ENOENT: no such file or directory, posix_spawn 'stdio'")
CTX = row("plugin:context-mode:context-mode", "limited", 1, label="context-mode", src="plugin")
REST = [row("claude.ai Gmail", "needs-auth", src="claudeai"), row("muninn", "connected", 45), row("quiet", "connected", 0),
        row("solo", "connected", 1), row("off", "disabled"), row("odd", "weird")]
PARTIAL = [BROKEN, CTX, REST[0], row("slow", "pending"), *REST[1:]]
FINAL = [BROKEN, CTX, REST[0], REST[1], REST[2], row("slow", "connected", 5), *REST[3:]]
FIXED = [CTX, REST[0], row("broken-tool", "connected", 3), REST[1], REST[2], row("slow", "connected", 5), *REST[3:]]
MCP = {"n": 0, "forced": 0, "old": False, "final": FINAL, "shown": [], "asked": []}


def mcp_answer(force):
    """What McpCheck.status gives: at once, the last list while a new check runs, then the list as it fills."""
    if force:
        MCP["forced"] += 1
        MCP["n"], MCP["final"] = 0, FIXED
    MCP["n"] += 1
    if MCP["n"] == 1:
        return {"servers": MCP["shown"], "at": AT if MCP["shown"] else 0, "checking": True, "error": "", "folder": True}
    if MCP["n"] == 2:
        return {"servers": PARTIAL if MCP["final"] is FINAL else MCP["final"], "at": 0, "checking": True, "error": "", "folder": True}
    MCP["shown"] = MCP["final"]
    return {"servers": MCP["final"], "at": AT, "checking": False, "error": "", "folder": True}


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
        p, _, query = self.path.partition("?")
        if p == "/api/projects":
            return self.send_json([{"id": "demo", "name": "demo", "path": TMP, "board": os.path.join(TMP, ".task_board")}])
        if p == "/api/tasks":
            return self.send_json(BOARD)
        if p == "/api/agents":
            return self.send_json({})
        if p == "/api/app-copy":
            return self.send_json({"current": ROOT, "copies": [{"path": ROOT, "main": True}]})
        if p == "/api/mcp":
            MCP["asked"].append(query)
            if MCP["old"]:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                return self.end_headers()
            return self.send_json(mcp_answer("force=1" in query))
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

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        return self.send_json({"ok": True})

    do_PUT = do_POST


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


DRIVER = r"""
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
const [url, chrome, dport, profile, shot, mode] = process.argv.slice(2);
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
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  else if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
});
const send = (method, params = {}) => new Promise(r => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, replMode: true }); return r.result.exceptionDetails ? { exc: r.result.exceptionDetails.exception?.description } : r.result.result.value; };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const until = async (expr, tries = 100) => { for (let i = 0; i < tries; i++) { if (await ev(expr) === true) return true; await sleep(100); } return false; };
await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('task-board:theme', 'light'); } catch (e) {}` });
const load = async () => { await send('Page.navigate', { url }); await until(`typeof polledOnce !== 'undefined' && polledOnce`); };
const BTN = `(() => { const b = document.querySelector('#mcp-btn'); return { hidden: b.hidden, display: getComputedStyle(b).display,
  badge: document.querySelector('#mcp-n').textContent, title: b.title, expanded: b.getAttribute('aria-expanded') }; })()`;
const POP = `(() => { const m = document.querySelector('#mcp-pop'), b = document.querySelector('#mcp-btn');
  const r = (e) => { const x = e.getBoundingClientRect(); return [Math.round(x.left), Math.round(x.top), Math.round(x.right), Math.round(x.bottom)]; };
  return { hidden: m.hidden, head: m.querySelector('.pop-head')?.firstChild?.textContent, when: m.querySelector('.pop-head small')?.textContent,
    rows: [...m.querySelectorAll('.mcp-list li')].map(li => [li.querySelector('.nm').textContent, li.querySelector('.st').textContent,
      li.querySelector('.why')?.textContent || '', li.className, li.title]),
    dots: [...m.querySelectorAll('.mcp-list li')].map(li => getComputedStyle(li.querySelector('.dot')).backgroundColor),
    notes: [...m.querySelectorAll('p')].map(p => p.textContent), again: { text: m.querySelector('#mcp-again')?.textContent, disabled: m.querySelector('#mcp-again')?.disabled },
    pop: r(m), btn: r(b), view: [innerWidth, innerHeight], clientWidth: document.documentElement.clientWidth }; })()`;
const out = {};
await load();
if (mode === 'old') {
  await sleep(1500);
  out.button = await ev(BTN);
  out.errors = errors;
  console.log(JSON.stringify(out));
  quit(0);
} else {
  out.settled = await until(`!!(mcp.data && !mcp.data.checking)`);
  out.button = await ev(BTN);
  await ev(`document.querySelector('#mcp-btn').click()`); await sleep(400);
  out.open = await ev(POP);
  out.buttonOpen = await ev(BTN);
  const shotData = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(shot, Buffer.from(shotData.result.data, 'base64'));
  await ev(`document.querySelector('#mcp-again').click()`);
  out.sawChecking = await until(`document.querySelector('#mcp-pop .pop-head small').textContent === 'Checking…' && document.querySelector('#mcp-again').disabled === true`, 30);
  out.resettled = await until(`!!(mcp.data && !mcp.data.checking)`);
  await sleep(200);
  out.again = await ev(POP);
  out.buttonAgain = await ev(BTN);
  await ev(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))`);
  await sleep(200);
  out.closed = await ev(`document.querySelector('#mcp-pop').hidden`);
  await ev(`localStorage.setItem('task-board:lang', 'fr')`);
  await load();
  await until(`!!(mcp.data && !mcp.data.checking)`);
  await ev(`document.querySelector('#mcp-btn').click()`); await sleep(400);
  out.fr = await ev(POP);
  out.frTitle = (await ev(BTN)).title;
  out.errors = errors;
  console.log(JSON.stringify(out));
  quit(0);
}
"""

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok  " if ok else "FAIL") + f" {name}" + ("" if ok else f": got {got!r}, want {want!r}"))


def drive(port, mode):
    driver = os.path.join(TMP, "driver.mjs")
    open(driver, "w").write(DRIVER)
    p = subprocess.run([NODE, driver, f"http://127.0.0.1:{port}/", CHROME, str(free_port()), os.path.join(TMP, "chrome-" + mode),
                        SHOT, mode], capture_output=True, text=True, timeout=120)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"error": f"driver gave no result\nstdout: {p.stdout[-2000:]}\nstderr: {p.stderr[-2000:]}"}


def main():
    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        res = drive(port, "menu")
        asked = list(MCP["asked"])
        MCP["old"] = True
        old = drive(port, "old")
    finally:
        srv.shutdown()
    for r in (res, old):
        if "error" in r:
            print("FAIL", r["error"])
            return 1
    check("no page errors", res["errors"] + old["errors"], [])
    check("the check finished on the page", (res["settled"], res["resettled"]), (True, True))
    b = res["button"]
    check("the button shows, with the count of the ones that do not work", (b["hidden"], b["display"] != "none", b["badge"]), (False, True, "2"))
    o = res["open"]
    check("the menu opens under the button", (o["hidden"], res["buttonOpen"]["expanded"], o["head"]), (False, "true", "MCP servers"))
    check("every server is listed in the order the server gave, with its state in plain words",
          [r[:2] for r in o["rows"]],
          [["broken-tool", "Does not work"], ["context-mode", "Works only in part"], ["claude.ai Gmail", "Needs sign-in"],
           ["muninn", "Works · 45 tools"], ["quiet", "Works"], ["slow", "Works · 5 tools"], ["solo", "Works · 1 tool"],
           ["off", "Switched off"], ["odd", "weird"]])
    check("a broken server says why, a half-working context-mode says what it needs, the others say nothing more",
          [r[2] for r in o["rows"]],
          ["ENOENT: no such file or directory, posix_spawn 'stdio'", "Only its health check loads. It needs a newer Node.",
           "", "", "", "", "", "", ""])
    check("each row carries its state as a class and the full name as its tip",
          [r[3:] for r in o["rows"][:3]] + [o["rows"][-1][3:]],
          [["s-failed", "broken-tool"], ["s-limited", "plugin:context-mode:context-mode"], ["s-needs-auth", "claude.ai Gmail"], ["s-weird", "odd"]])
    check("the dots differ: broken, half working, sign-in, working, off", len({o["dots"][i] for i in (0, 1, 2, 3, 7)}), 5)
    check("it says when it was checked, and Check again is ready", (o["when"].startswith("Checked at "), o["again"]), (True, {"text": "Check again", "disabled": False}))
    check("no warning lines when all is fine with the check itself", o["notes"], [])
    pl, pt, pr_, pb = o["pop"]
    bl, bt, br, bb = o["btn"]
    check("the menu sits inside the window, under the button, lined up with its right edge",
          (pl >= 8, pr_ <= o["view"][0] - 8, pb <= o["view"][1], pt == bb + 6, pr_ == br), (True, True, True, True, True))
    check("the page is laid out at the test width", o["clientWidth"], 1400)
    check("Check again asks the server for a fresh check", (MCP["forced"], [q for q in asked if "force=1" in q]), (1, ["p=demo&force=1"]))
    check("while it checks the menu says so and the button is off", res["sawChecking"], True)
    a = res["again"]
    check("then the new list shows: the fixed server works again", [r[:2] for r in a["rows"]][:3],
          [["context-mode", "Works only in part"], ["claude.ai Gmail", "Needs sign-in"], ["broken-tool", "Works · 3 tools"]])
    check("and the badge counts one fewer", res["buttonAgain"]["badge"], "1")
    check("Escape closes the menu", res["closed"], True)
    fr = res["fr"]
    check("French: the states and the head are translated", ([r[1] for r in fr["rows"]][:4], fr["head"], fr["again"]["text"]),
          (["Ne fonctionne qu’en partie", "Connexion requise", "Fonctionne · 3 outils", "Fonctionne · 45 outils"], "Serveurs MCP", "Vérifier à nouveau"))
    check("French: the button's tip too", res["frTitle"], "Les serveurs MCP que Claude peut utiliser dans ce projet, et si chacun fonctionne")
    ob = old["button"]
    check("a server with older code (404): the button stays hidden", (ob["hidden"], ob["display"]), (True, "none"))
    print("screenshot:", SHOT)
    print("ALL PASS" if not fails else f"{fails} FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
