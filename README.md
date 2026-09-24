# onemoretask

A local kanban board with subtasks that hands cards to Claude Code sessions and shows
their progress on the card.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/astanar01/onemoretask/main/install.sh | sh
```

This downloads the repo to `~/.onemoretask` (set `ONEMORETASK_DIR` to change it), links the
`onemoretask` command into `~/.local/bin`, and adds that folder to your PATH if needed. Run it
again to update. Already cloned the repo? Run `./install.sh` inside it instead.

Needs `python3`, `git`, `curl`, and the Claude Code CLI (`claude`) for Send to Claude.

## Run

```bash
onemoretask [--port 8765] [--project PATH]
```

It starts the server and opens http://127.0.0.1:8765 in the browser, or only opens the page if
the server is already running. Ctrl-C stops it. (`python3 server.py` still works too.)

Agents post to their card with `report.py` (progress / question / done). Each project
keeps its board in `<project>/.task_board/` — or `<project>/tools/task_board/` when that
already holds a `tasks.json`. Remembered projects: `~/.config/task_board/projects.json`.

Full behaviour: the header docstring of `server.py`; board layout: `reports.py`.
