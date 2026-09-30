# Board conventions

Rules for changing the board, learned the hard way.

## "Always do X when Y happens" goes where Y is seen

If a rule fires on a user action on the board (a card moves to done, a column
changes), put it in `server.py`'s save path (`PUT /api/tasks`: diff the old and new
task lists), not in an agent prompt. The agent is usually gone when the user clicks
"Move to done", so a prompt rule can't keep the promise. Example: the changelog
(`changelog.py`, called from `PUT /api/tasks`).

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

## Screenshots of the running board

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
