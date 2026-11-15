---
name: ctx-rules
description: "Use ctx_search before re-running commands this session. Triggers on: search indexed output, check prior results, avoid re-fetching, session memory, retrieval protocol, anti-patterns, ctx_execute vs Bash, when to use ctx_search."
---

## Context Mode (session memory)

The full protocol is the always-loaded `~/.claude/rules/ctx-rules.md`; this skill exists because
session hooks invoke `Skill('ctx-rules')`. Re-read that file's sections when needed:

- **What gets indexed (2.x)**: `ctx_batch` always, `ctx_execute` with `intent` (or output over
  16 KB), `ctx_index` (`path`, `content` or `url`). Plain Bash/Read/Grep output is not searchable later.
- **Retrieval protocol**: `ctx_search` before re-running anything; about one search per 3–5 indexing operations.
- **Anti-patterns**: never cap output with `head`/`tail`/`sed -n`/`grep -m` in captured commands; filter in code.
- **Unknown fields are rejected**: never pass `cwd`, `label`, `project` or `concurrency` to a ctx_* tool.
- **Reading a file**: `ctx_execute` with `file`; the script opens the path in `CTX_FILE` (binary mode for assets).
- **Only `ctx_doctor` listed, or `ctx_batch` missing**: Node is older than 24.15.0 or the plugin is
  still 1.x (`ctx_batch_execute`, `ctx_execute_file`, `ctx_fetch_and_index`). Re-run the board's
  setup, restart Claude Code, run `/ctx-upgrade`.
