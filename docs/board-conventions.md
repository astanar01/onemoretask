# Board conventions

Rules for changing the board, learned the hard way.

## "Always do X when Y happens" goes where Y is seen

If a rule fires on a user action on the board (a card moves to done, a column
changes), put it in `server.py`'s save path (`PUT /api/tasks`: diff the old and new
task lists), not in an agent prompt. The agent is usually gone when the user clicks
"Move to done", so a prompt rule can't keep the promise. Example: the recap of a
card moved to done (`WATCH.close_when_idle`, called from `PUT /api/tasks`).

## Testing against a live server

- Rebuild each step's payload from the server's current file (`GET` or read
  `tasks.json`), not from a shell variable saved earlier. Leftover state from one step
  fakes a failure in the next.
- Use a throwaway project on a spare port for anything that writes. Never test
  writes on the user's real board.
- A spare port isolates the network, not the disk: `server.py --project <dir>` adds
  `<dir>` to `~/.config/task_board/projects.json`, the registry the real board reads.
  Start the test server with `HOME=<tmp>/home` so the throwaway project never lands
  there.
- That fake HOME also logs out every CLI backend. For a Codex test keep its login with
  `CODEX_HOME=~/.codex`; `claude agents --json --all` fails in a fresh HOME, so the
  launcher replaces `server.list_sessions` with `lambda cwd: []` before `server.main()`
  (`--port`, `--project`). An API-driven test must `PUT` `task.agent` itself: the page
  normally does it after `/api/agent`.
- When the real board is already running, serve `server.Handler` under a
  `ThreadingHTTPServer` from a small script instead of `server.py main()`: `main()`
  also starts the session watcher, and a second watcher would act on live sessions.
- To screenshot a card's overview with made-up agent data (skills, subagents), replace
  `server.WATCH.snapshot` in that script with a function returning the entry, and make the
  handler subclass answer 403 to `PUT`/`POST`/`DELETE` so the page cannot write to the real
  board. Never call `WATCH.poll()` there: it delivers notes and closes sessions.
- HTML5 drag-and-drop (card reorder, column moves) needs synthetic `DragEvent`s
  (`dragstart` → `dragover` → `drop` → `dragend`) sharing ONE `DataTransfer`,
  dispatched to `document.elementFromPoint(x, y)` at the target point.
- **Card states that need a finished session** (buttons shown only in phase "finished")
  without a paid run: a ~50-line script that sets `HOME=<tmp>/home`, imports the real
  `server`, replaces `start_session`, `resume`, `list_sessions` (returns every started
  fake id as idle), and `run_claude` (raises), then runs the REAL
  `WATCH.poll()` in a 1 s loop and serves `server.Handler` on a spare port. With every
  session call faked and its own HOME, this poll cannot touch live sessions (unlike the
  overview script above). Play Claude's side from the shell with the real `report.py`
  (`done "... <sha>"`) and real commits. The default-model probe hits the `run_claude`
  guard; that error is harmless.
- **What the CLI shows for a session state** (e.g. a permission prompt): recreate the
  state with a throwaway `claude --bg --permission-mode default` session (from a
  trusted folder — a scratch dir fails with "Workspace not trusted"), then read
  `claude agents --json --all` and `~/.claude/jobs/<id>/state.json`. Then `claude stop`
  and `claude rm` it. The field is often already there.

## Measuring card sessions from transcripts

A card session in `~/.claude/projects` is one whose FIRST non-meta user message starts
with the card prompt ("Work on this task from the project task board") and whose project
folder is not a temp dir (`-T…`, `var-folders`). Matching the card text anywhere in a
transcript also picks up the recap runs (`claude -p` in a temp dir, no tools), which embed
it: once 158 of 159 matches were recaps and every number on the card was wrong. Before
reporting numbers, check the sample: does it have `tool_use` calls, and are sizes plausible?

## README screenshots (`docs/screenshots/`)

The cards' live states (working, needs you, permission, subagents) come from running
sessions, so a plain demo board cannot show them. What worked:

- A script builds a throwaway project: a real git repo with a worktree and commits, board
  files and an observation log.
- The real server runs on a spare port with `HOME=<tmp>/home`, so no real data or paths
  show and the real project registry is untouched.
- Playwright stubs only the live-state routes (`/api/agents`, `/api/task-git`) with crafted
  JSON, keeping the server's headers via `route.fetch()`. Everything else is real.
- Shoot at `deviceScaleFactor: 2` and crop empty space with `clip`.
- Before committing, scan every shot (and the DOM before the shot) for real usernames and
  absolute paths, and look at each image.

Keep the builder and shot script in the repo next to the images (e.g.
`tools/screenshots/`). The first harness lived in a job tmp dir, was deleted, and had to be
re-derived for the next refresh.

## Screenshots of the running board

To check a page change from a worktree on REAL data without a second server: a tiny
Python proxy serves the worktree's `index.html` at `/` and forwards only `GET`s to the
running board on :8765 (any other method gets 403, so the page cannot write). Point the
headless browser at the proxy.

Use the `run-extras` skill (`scripts/cdp-shot.mjs`) when the Chrome extension is
offline. Two board-specific traps:

- A fresh browser profile opens whatever project is the default, not yours. Put
  `#p=<project id>` in the URL: `http://127.0.0.1:8765/#p=<id>`. Ids are hashes: get
  yours from `GET /api/projects` by matching its `path` field.
- Open a card and measure in one expression, e.g.
  `node ~/.claude/skills/run-extras/scripts/cdp-shot.mjs 'http://127.0.0.1:8765/#p=<id>' out.png 1440 900 'openDetail("<task id>"); await new Promise(r=>setTimeout(r,500)); [...document.querySelectorAll(".panel-cols > *")].map(e=>e.getBoundingClientRect().width)'`.
- The board re-renders on a poll and undoes page-only tweaks (e.g. forcing a hidden
  Send row visible). Make the tweak in the same expression that measures.
- Opening a card (`openDetail(id)`) can clear its unread mark on the user's real
  board. Pick cards that are not in `/api/projects` → `attention`, or use a separate
  server.

**No real data needed (how card text renders, List view layout):** `tests/ui_stub/`.
`stub_server.py <checkout> <port> [agent_reports/<id>.json]` serves the checkout's real
page and fakes only `/api/projects`, `/api/tasks`, `/api/agents` (one demo card whose
log ends with `sample_done.md`). Then `node shot.mjs <port> out.png [light|dark]` shoots
the detail panel, `node shot_list.mjs <port> out.png <width>` the List view. Run the
`.mjs` files from a temp dir with `npm i playwright-core@1.48.2` (it uses the installed
Chrome). Traps:

- Each `/api/projects` entry needs a `board` field, or no cards render ("reading
  'replace'").
- The theme comes from `colorScheme` plus `localStorage` `task-board:theme`.
- A page error "reading 'find'" from the thin agent stub is harmless.
