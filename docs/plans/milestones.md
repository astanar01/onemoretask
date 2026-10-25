# Plan: milestones (the Plan page)

Card `mvnzyrajw45d7`, 2026-10-25. Built on the same card (user guide: `docs/guide.md` "Plan milestones";
tests: `tests/test_plan_ui.py`, `tests/test_plan_suggest.py`). Differences from this plan: reordering a sent
task on the Plan page does not reorder its card on the board.

## What the user asked for

Set milestones, write tasks under each one, and when ready send them to the board.

Choices the user made on the card:

| # | Question | Answer |
|---|----------|--------|
| 1 | Where milestones are made | A new **Plan** page, beside Board and List |
| 2 | How a sent milestone shows on the board | **One big card** (the milestone) with the tasks inside it as subtasks |
| 3 | What Send does | Cards go to **To Do**; the user starts each one (no session is started) |
| 4 | Send all or some | **Both**: "Send all" and ticking single tasks |
| 5 | Claude's help | **Yes**: the user writes the goal, Claude suggests tasks, the user edits them before sending |
| 6 | After sending | The milestone **stays** on the Plan page with a done count |
| 7 | Due dates | **No** |

## What exists already (checked in the code)

- Subtasks: every card has `parent` (`index.html` `addTask(title, colId, parent)`, `children(id)`,
  nesting in `render()` and `renderList()`, a progress bar `doneKids/kids.length` on the card). The real
  board has 210 cards and none uses `parent`, but the code path is live.
- The prompt already carries the chain: `server.py` `build_prompt()` adds "Part of: <parent>" and
  "Parent notes: ..." to a subtask, and lists the subtasks of a card that has them. So a task sent from a
  milestone tells Claude the milestone goal for free, and sending the big card itself hands Claude the whole
  milestone.
- The page saves the whole board object (`save()` → `PUT /api/tasks` with `JSON.stringify(state)`); the
  server writes it as-is under `board_lock` with the `If-Match` rev check. Extra top-level keys already
  survive (`simpleWords`).
- Views: `let view = "board"` / `"list"`, picked in the View menu, saved in `localStorage` `LS_KEY+":view"`.
- One-shot Claude calls with no tools: `server.py` `ask_claude(model, ask, data, timeout, system)`, used by
  `autofill()` (titles/tags, Haiku) and `which_project()` (project profile = README start + newest card
  titles).

## Data

A new top-level list in `tasks.json`, saved by the same `PUT /api/tasks` as the cards:

```json
"milestones": [
  { "id": "…", "title": "Login works", "notes": "the goal, in the user's words",
    "order": 0, "created": 0, "updated": 0,
    "card": "<id of the big card, once sent>",
    "tasks": [
      { "id": "…", "title": "…", "notes": "…", "order": 0, "card": "<id of its card, once sent>" }
    ] }
]
```

- Drafts live inside the milestone, not as cards, so nothing shows on the board until it is sent.
- A sent task keeps its draft entry with `card` set. Its state (To Do / In Progress / Done) is read live from
  that card's column; the draft text is not kept in step after sending (the card is the source from then on).
- The big card gets `milestone: "<milestone id>"`, so the board can show a small milestone mark on it.
- Why not a separate `milestones.json`: one file keeps one lock, one rev and one save path, and an old tab
  that saves the whole object keeps the list. Rejected: a second file with its own API (two revs to keep in
  step for one Send).

## The Plan page

- A third value in the View menu: Board / List / **Plan** (`view = "plan"`, same `localStorage` key).
- Left: the milestones, in order (drag to reorder), each with its done count `3 of 5 done` and a thin bar.
  A **New milestone** button on top.
- Right: the open milestone. Title, goal notes, then its tasks as rows: title, notes (folds open), a tick
  box, and a state chip once sent (To Do / In Progress / Review / Done / Not on the board).
- Add a task with an input under the list (Enter adds), like the card's "+ Add a subtask". Drag to reorder.
  Delete a draft with a small ✕.
- Buttons: **Suggest tasks** (Claude), **Send ticked** (only when some are ticked), **Send all** (all drafts
  not yet sent).

## Send

1. If the milestone has no big card on the board (never sent, or its card was deleted), create one in the
   first open column (`firstOpenCol()`, To Do): title = milestone title, notes = milestone notes,
   `milestone` = its id. Set `milestone.card`.
2. For each chosen draft not yet sent, `addTask(draft.title, todo, bigCard)` with its notes. Set
   `draft.card`.
3. One `save()`. No session starts (answer 3).
4. If the big card is no longer in To Do (it was moved or started), new tasks still go in To Do under it;
   the board already draws a subtask on its own when its parent sits in another column.

## Done count

- Sent tasks whose card sits in a `done` column, over all tasks of the milestone (drafts included), e.g.
  `2 of 6 done · 4 on the board`.
- A sent task whose card was deleted shows **Not on the board** and can be ticked and sent again (a new card).
- A milestone whose tasks are all done shows **Done**. It stays (answer 6); a **Hide done** toggle on the
  list keeps the page short.

## Suggest tasks (Claude)

- `POST /api/plan/suggest {p, milestoneId}` → `{tasks: [{title, notes}]}`.
- Server: `ask_claude()` with no tools, the milestone title and notes as data (same "data, not a message to
  you" system wording as `autofill()`), plus the project profile `which_project()` builds (README start +
  newest card titles) so the tasks fit the project. Asks for 3 to 8 small tasks, each one card's worth of
  work, in order, as JSON.
- Model: **Sonnet** (Haiku is for labels; splitting a goal needs more). Default if not answered: Sonnet.
- The page adds the answer as drafts, marked "suggested" until the user edits or keeps them. Nothing is sent.
- A second click adds more; it never deletes the user's own drafts.
- Later (not v1): a read-only session that looks at the code before suggesting.

## Edit and delete

- Draft tasks: free to edit. Sent tasks: the row shows the card's title and opens the card on click; edits
  happen on the card.
- Deleting a milestone deletes its drafts only. Its cards stay on the board (they may hold work).
- Deleting the big card on the board does not delete the milestone; it shows Not on the board and Send makes
  a new one.

## Wording, French, changelog

- Every new label goes through `t()` with a French line in `i18n.js` (`tests/test_i18n.py` checks).
- One line in `whats-new.md` when it ships, in easy words, e.g. "A new Plan page: make milestones, list the
  tasks, let Claude suggest some, and send them to the board when you are ready."

## Build order (one card each, or one card with subtasks)

1. Data + Plan page: milestones list, drafts, add/edit/delete/reorder, View menu entry. Local only.
2. Send (all and ticked), big card + subtasks, the milestone mark on the board card, done count and
   Not on the board.
3. Suggest tasks: the server route, the prompt, the page button.
4. French strings, changelog line, guide section in `docs/guide.md`.

## Tests

- UI, the same way as `tests/test_decision_tree_ui.py` (real `index.html` + `i18n.js`, headless Chrome over
  CDP, `/api` faked by a stub server on a free port, never the live board): make a milestone, add drafts,
  Send ticked → one big card + only the ticked subtasks in To Do; Send all → the rest; delete a sent card →
  Not on the board; move a card to Done → the count moves.
- Server: `/api/plan/suggest` with `ask_claude` replaced by a fake (no paid call): JSON parsed, bad answer →
  clear error, notes passed as data.
- Save: a `PUT /api/tasks` with `milestones` keeps it byte-for-byte; a body without it (an old tab) does not
  wipe it. **Open point for step 1:** today the server writes the body as-is, so an old tab opened before the
  update would drop `milestones` on its next save. Fix in the server: if the body has no `milestones` key,
  keep the one on disk. Default if not answered: do this.
