# onemoretask

A local kanban board that hands cards to Claude Code. Write a task, click **Send to Claude**,
and the agent works on it in the background, posting its progress, questions and results
back onto the card.

![The board: one task Claude is working on, one waiting on you, one finished](docs/screenshots/board.png)

| Claude asks you a question | Claude reports it is done |
|---|---|
| ![A card with Claude's question and a reply box](docs/screenshots/question.png) | ![A card with Claude's progress and final report](docs/screenshots/finished.png) |

## Requirements

- macOS or Linux
- `python3`, `git`, `curl`
- [Claude Code](https://claude.com/claude-code) (the `claude` command), for Send to Claude

## Install

Run this in a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/astanar01/onemoretask/main/install.sh | sh
```

It:

- downloads the app to `~/.onemoretask`
- adds the `onemoretask` command to `~/.local/bin`
- adds `~/.local/bin` to your PATH if it isn't there yet (then open a new terminal)

Already cloned the repo? Run `./install.sh` inside it instead. It uses that copy.

## Run

```bash
onemoretask
```

This starts the board and opens http://127.0.0.1:8765 in your browser. If the board is
already running, it just opens the page. Press **Ctrl-C** in the terminal to stop it.

### Options

| Option | What it does |
|---|---|
| `--port 9000` | Use another port (default 8765) |
| `--project PATH` | Open this folder's board |
| `--permission-mode MODE` | Permission mode for agents started with Send to Claude (default `auto`) |

Without `--project`, the board opens the projects you used before. The first time, it opens
the git repo you run it from.

## Use

1. **Pick a project.** The project menu at the top switches boards. **Open folder…** adds any
   folder. Each project keeps its board in `<project>/.task_board/`. Commit `tasks.json` and
   `images/`; `agent_reports/` is run chatter and stays out of git.
2. **Add a task.** Give it a title, notes, subtasks. You can paste images into the notes.
3. **Send to Claude.** Pick a model (or leave the default) and send. A background Claude
   session starts in the project folder with the task as its prompt. Tick
   **Divide in subtasks / use subagents** to let it split the work.
4. **Follow the card.** It shows whether the agent is working, needs you, or is finished,
   and on macOS you get a notification when it needs you or finishes.
5. **Reply on the card.** Answer questions or ask for changes. The reply resumes the same
   session.

To see a session in the terminal: `claude agents` lists them and `claude attach <id>` opens one.

### Posting from an agent

Agents post to their card with `report.py`. The prompt Send to Claude builds already
tells them how:

```bash
python3 report.py --board <project>/.task_board <task_id> progress "what I'm doing now"
python3 report.py --board <project>/.task_board <task_id> question "what I need you to decide"
python3 report.py --board <project>/.task_board <task_id> done "what I did, what is left"
```

Add `--image <file>` to attach a screenshot.

## Update

Run the install command again. It pulls the latest version into `~/.onemoretask`.
Installed from your own clone? Run `git pull` in it instead.
The running board restarts itself when its code changes.

## Uninstall

```bash
rm ~/.local/bin/onemoretask
rm -rf ~/.onemoretask
rm -rf ~/.config/task_board   # remembered projects and settings
```

Your boards stay in each project's `.task_board/` folder.

## More

Full behaviour: the header docstring of `server.py`. Board storage: `reports.py`.
