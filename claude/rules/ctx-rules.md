## Context Mode (session memory)

Context-mode 2.x keeps a per-project FTS5 index in `~/.context-mode/projects/<key>/context.db`, kept across sessions. It needs Node 24.15.0 or newer: on an older Node its server offers only `ctx_doctor` and its hooks do nothing. Only output that goes through a ctx_* tool is searchable with `ctx_search`. Every tool rejects an unknown input field (`UNKNOWN_FIELD`): never pass `cwd`, `label`, `project` or `concurrency`. Facts below are from the upstream docs and source (docs/tools.md, src/core/tools/execute.ts), not yet probed live on 2.x. A machine still on 1.x uses the old names (`ctx_batch_execute`, `ctx_execute_file`, `ctx_fetch_and_index`); see "1.x names" at the end.

### What gets indexed

| Call | Raw output searchable? |
|------|------------------------|
| `ctx_batch` | YES, always |
| `ctx_execute` with `intent`, any size | YES; the reply is the chunks that match the intent |
| `ctx_execute` without `intent`, output over 16,384 B | YES; the reply is a 4 KB preview plus a ctx_search line |
| `ctx_execute` without `intent`, output up to 16,384 B | NO; it comes back inline |
| `ctx_index` (`path`, `content` or `url`) | YES |
| Plain `Bash` / `Read` / `Grep` / `WebFetch` | NO, when the large-output replace is off (see below) |

**Large-output replace.** 2.x can replace any host tool output over a size limit with a preview plus a `ctx_search` line. The board's setup sets `CTX_REPLACE_TRIGGER_BYTES=1048576` in `~/.claude/settings.json` (`env`) when you have not set it yourself, which in practice turns the replace off (0, empty or invalid values fall back to 4096; there is no off switch). With it off, plain Bash/Read/Grep output is not stored.

So: if you may want to search output later, run it through `ctx_batch` (or `ctx_index` it). Do not assume a Bash call is recoverable by `ctx_search`.

| Tool | Purpose |
|------|---------|
| `ctx_execute` | Run code or a shell command in a sandbox (`language`, `code`, optional `file`, `intent`, `timeoutMs`, `background`, `summary`); only what it prints returns, capped at 16,384 B. Always runs in the project worktree. With `file`, the script gets that file's absolute path in the env var `CTX_FILE` and must open it itself; the path must be inside the project. Use it instead of Read when you only need a computed result from a file. To inspect a binary asset (GLB, PNG, font), open `CTX_FILE` in binary mode in the code. |
| `ctx_batch` | Run several commands and search their output in one call. `commands` = array of command strings (no `{label, command}` objects); `queries` is REQUIRED. Put a literal word from each command in `queries` if you need to find its section. |
| `ctx_index` | Index a file or folder (`path`), a text (`content`) or a URL (`url`), optional `source` label. |
| `ctx_search` | Search the index: `queries[]`, `scope` (`session`, `worktree` default, `project`), `source`, `sort` (`relevance`, `timeline`), `offset`, `after`. 5 hits per reply; a `More:` line pages on with `offset`. No `project` field. |
| `ctx_resume` | Return the session state block (max 2,048 B), or one full row by `seq`. Use after a resume or compaction. |
| `ctx_stats` | Show how much output stayed out of context, per session, project and all time. |
| `ctx_doctor` | Check the install; each FAIL row names its fix. Read-only. |
| `ctx_upgrade` | Install, upgrade or uninstall. Operator use only: never call it without the user asking. |
| `ctx_purge` | **Destructive: deletes indexed data by session, source or project.** Never call it without an explicit user request. |

### Retrieval protocol

Before `ctx_execute` on a topic you have already investigated, try `ctx_search(queries=["<topic>"])` first. If it returns useful hits, read them instead of re-running the command. Aim for roughly one `ctx_search` per 3-5 indexing operations (`ctx_batch` / `ctx_index` / `ctx_execute` with intent); if you have indexed five things without searching once, you are almost certainly re-fetching something you already have. Plain Bash/Read/Grep output is not in the index; re-run it through `ctx_batch` if you need to search it.

### Prefer

Prefer `ctx_index(url=...)` over `ctx_execute("curl ...")` for URL fetches. When spawning in an established session, call `ctx_stats` first to see what is already indexed before deciding what to fetch or run.

### Anti-patterns

- **Never use output-truncation primitives (`head -N`, `tail -N`, `sed -n '1,Np'`, `grep -m N`, `awk 'NR<=N'`) to cap large command output in captured shell commands.** Inside `ctx_batch` or a `ctx_execute` call, bytes cut before indexing are lost and unsearchable; in plain Bash they are simply gone (nothing indexes Bash output). Exception: bare `head file` / `tail file` (reading a small file directly) is fine; only the `cmd | head -N` pipe-to-truncate pattern is the anti-pattern. Write programmatic analysis instead: filter, count and extract in code, then print only the findings. It is advisory: no PreToolUse hook enforces it on sandbox payloads.
- **Never pass a field a tool does not list** (`cwd`, `label`, `project`, `concurrency`): 2.x rejects the whole call.

### 1.x names

If `ctx_batch` is missing from the tool list, the machine still runs 1.x (or Node is too old: then only `ctx_doctor` shows). Run the board's setup again, restart Claude Code, then run `/ctx-upgrade`. Until then: `ctx_batch_execute` (commands are `{label, command}` objects) instead of `ctx_batch`, `ctx_execute_file` instead of `ctx_execute` + `file`, `ctx_fetch_and_index` instead of `ctx_index` + `url`; `ctx_resume` does not exist.
