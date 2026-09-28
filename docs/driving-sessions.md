# Driving a live Claude Code session from outside

How the board types into a running session (`attach.py`), and how to test it safely.

## Always go through attach.py

`attach.py` runs `claude attach <id>` in a pseudo-terminal and refuses to type when a
permission prompt or question menu is on screen (`DIALOG`). Test with it:

```bash
python3 attach.py <session short id> "<text>"
```

Never probe with a hand-made pty script. One once pasted a note while a Bash
permission prompt was showing, and the paste + Enter approved the command. Any new
probe must check the screen for a dialog before every keypress.

## The keys (measured)

- **Ctrl+B** sent through `claude attach` does nothing.
- **Ctrl+X Ctrl+S** ("send now", shown as a hint while a typed message waits in the
  queue) delivers the queued message in about 3 s, with side effects:
  - a running shell command moves to the background (it is not killed);
  - a reply being written stops where it is, and the message becomes the next turn.

## Timing delivery

Don't time it from screen scrapes. Read the session's transcript
(`~/.claude/projects/<dir>/<session>.jsonl`) for its `queue-operation` /
`queued_command` entries.
