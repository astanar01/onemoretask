# onemoretask

A local kanban board with subtasks that hands cards to Claude Code sessions and shows
their progress on the card.

```bash
python3 server.py [--port 8765] [--project PATH]   # then open http://127.0.0.1:8765
```

Or, after `ln -s "$PWD/bin/onemoretask" ~/.local/bin/` (any folder on your PATH), just run
`onemoretask [--port N] [--project PATH]`: it starts the server and opens the board in the
browser, or only opens the board if the server is already running. Ctrl-C stops it.

Agents post to their card with `report.py` (progress / question / done). Each project
keeps its board in `<project>/.task_board/` — or `<project>/tools/task_board/` when that
already holds a `tasks.json`. Remembered projects: `~/.config/task_board/projects.json`.

Full behaviour: the header docstring of `server.py`; board layout: `reports.py`.
