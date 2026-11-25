"""A run that stopped on a progress note (a reply answered without a done report) keeps the card in In Progress.
The panel's Next action must still offer "Merge worktree" while the worktree holds unmerged work, and not when it
holds none, when Claude asked a question, or for a card with no worktree.

Runs the real nextAction() from index.html in node, with the page globals it reads stubbed.
Run: python3 tests/test_stopped_merge_button.py   INDEX=<file> tests another index.html (e.g. the previous commit's)."""
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.environ.get("INDEX") or os.path.join(ROOT, "index.html")
NODE = os.environ.get("NODE") or shutil.which("node") or "/Users/antoinecm/.nvm/versions/node/v24.12.0/bin/node"


def function_source(html, name):
    start = html.index(f"function {name}(")
    i = html.index("{", start)
    depth = 0
    for j in range(i, len(html)):
        depth += {"{": 1, "}": -1}.get(html[j], 0)
        if depth == 0:
            return html[start:j + 1]
    raise ValueError(name)


STUBS = r"""
globalThis.window = { t: (s, v) => s.replace(/\{(\w+)\}/g, (_, k) => (v && k in v ? v[k] : "{" + k + "}")) };
const useServer = true, pending = {}, permissionNote = {}, answering = {}, CLI_NAMES = {};
const $ = () => ({ focus() {} });
const who = () => "Claude", cliOf = () => "claude", replyOpen = () => true, isDone = () => false;
const reviewing = () => false, claudeReviewed = () => false, canReview = () => false, canFinish = () => false;
const inReview = () => false, canWorktree = () => false, colRole = () => null, mcpServers = () => "", agyStep = () => "";
const cpReady = () => false, worktreeLabel = () => "Merge worktree to main";
const noop = () => {}, showTab = noop, worktreeAction = noop, moveToDone = noop, closeDetail = noop, startReview = noop,
  sendFindingsBack = noop, answerPermission = noop, openFresh = noop, moveTask = noop, renderDetail = noop, render = noop,
  askRepo = noop, cpBuild = noop;
"""

CASES = r"""
const WT = { path: "/w", branch: "b", base: "main" };
const card = (extra = {}) => ({ id: "x", column: "prog", agent: { worktree: WT }, ...extra });
const progressLog = [{ from: "claude", status: "done", message: "Done." }, { from: "you", status: "reply", message: "so do i need to merge?" },
  { from: "claude", status: "progress", message: "Yes. Press Merge worktree to main." }];
const ag = (extra = {}) => ({ phase: "needs_you", reason: "Stopped without reporting — attach or reply to check on it", worktree: "/w", log: progressLog, ...extra });
const work = { available: true, changed: [], ahead: ["a", "b"], target: "main" };
const labels = (t, a, g) => nextAction(t, a, g).actions.map((x) => x.label);
console.log(JSON.stringify({
  stopped: labels(card(), ag(), work),
  noWork: labels(card(), ag(), { available: true, changed: [], ahead: [] }),
  gitNotLoaded: labels(card(), ag(), undefined),
  question: labels(card(), ag({ log: [...progressLog, { from: "claude", status: "question", message: "A or B?" }] }), work),
  noWorktree: labels({ id: "x", column: "prog", agent: {} }, ag({ worktree: null }), work),
  worktreeGone: labels(card(), ag({ worktree: null }), work),
  answerOnly: labels(card({ answerOnly: true }), ag(), work),
}));
"""


def main():
    html = open(INDEX, encoding="utf-8").read()
    src = STUBS + function_source(html, "nextAction") + CASES
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True)
    if out.returncode:
        print(out.stderr)
        sys.exit(1)
    r = json.loads(out.stdout)
    M = "Merge worktree to main"
    checks = [
        ("stopped run with unmerged commits offers Merge", M in r["stopped"]),
        ("stopped run still offers Reply first", r["stopped"][:1] == ["Reply in Conversation"]),
        ("no unmerged work: no Merge", M not in r["noWork"]),
        ("git state not loaded yet: no Merge", M not in r["gitNotLoaded"]),
        ("an open question: no Merge", M not in r["question"]),
        ("card without a worktree: no Merge", M not in r["noWorktree"]),
        ("worktree already gone: no Merge", M not in r["worktreeGone"]),
        ("answer-only card: no Merge", M not in r["answerOnly"]),
    ]
    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(("PASS " if ok else "FAIL ") + n)
    print(json.dumps(r))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
