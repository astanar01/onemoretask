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

## Answering a permission prompt

`attach.answer_prompt()` (the card's Allow / Allow always / Deny) presses a menu key only
when the screen shows a permission menu AND the command the card showed (compared with
spaces and box lines removed, so a wrapped command still matches). It looks only inside the
prompt box (below its top `────` line): the chat above can show other queued commands.
The card's command comes from the job's `state.json` `needs`, which the CLI cuts at 800
characters with a trailing `…` and with newlines turned into spaces; the `…` is dropped and
the rest must appear in the box. After the key, a prompt on screen does not mean "not
answered": with parallel tool calls the next queued prompt appears at once. It counts as
not answered only while the box still shows the same command. It picks the key by the
option's label, not its number: the menu varies (measured on a Bash prompt: `1. Yes`,
`2. Yes, and always allow access to <dir> from this project`, `3. Yes, and switch to auto
mode`, `4. No`). Allow always never picks the auto-mode option. Number keys answer at once,
no Enter needed.

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
