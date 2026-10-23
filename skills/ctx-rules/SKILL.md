---
name: ctx-rules
description: "Use ctx_search before re-running commands this session. Triggers on: search indexed output, check prior results, avoid re-fetching, session memory, retrieval protocol, anti-patterns, ctx_execute vs Bash, when to use ctx_search."
---

## Context Mode (session memory)

The full protocol is the always-loaded `~/.claude/rules/ctx-rules.md`; this skill exists because
session hooks invoke `Skill('ctx-rules')`. Re-read that file's sections when needed:

- **What gets indexed**: only `ctx_batch_execute`, `ctx_execute`/`ctx_execute_file` with `intent=`
  and output over 5 KB, `ctx_index`, `ctx_fetch_and_index`. Plain Bash/Read/Grep output is not searchable later.
- **Retrieval protocol**: `ctx_search` before re-running anything; about one search per 3–5 indexing operations.
- **Anti-patterns**: never cap output with `head`/`tail`/`sed -n`/`grep -m` in captured commands; filter in code.
- **`ctx_execute_file` is text-only**: open binary assets in `ctx_execute`.
