## Context Mode (session memory)

Context-mode is a session-scoped FTS5 index. Only output that goes through an indexing path is searchable with `ctx_search` — see the table below. `intent=` on `ctx_execute` / `ctx_execute_file` indexes the output ONLY when it exceeds `INTENT_SEARCH_THRESHOLD = 5_000` bytes (~80–100 lines); the tool then returns FTS5 hits + section titles instead of the raw output. Below 5KB, or with no `intent`, the output comes back inline and is NOT indexed.

### What gets indexed (probed 2026-09-30, plugin v1.0.166 / server v1.0.169)

Tested with a unique marker per call, then `ctx_search` for it:

| Call | Raw output searchable? |
|------|------------------------|
| `ctx_batch_execute` | YES — always, even tiny output |
| `ctx_execute` + `intent`, output > 5KB | YES |
| `ctx_execute` + `intent`, output < 5KB | NO |
| `ctx_execute` without `intent` (any size) | NO |
| Plain `Bash` / `Read` / `Grep` | NO |
| `ctx_index`, `ctx_fetch_and_index` | YES (by design; not marker-probed) |

The PostToolUse hook (`hooks/posttooluse.mjs`) does NOT index raw output. It calls `extractEvents()` and stores short typed events (bash_outcome, file_read, file_edit, git, error_tool, skill, decision...) in the session DB. Those events are searchable (best with `sort: "timeline"`) and feed resume/compaction, but the full output of a Bash or Read call is gone once it leaves context.

So: if you may want to search output later, run it through `ctx_batch_execute` (or `ctx_index` it). Do not assume a Bash call is recoverable by `ctx_search`.

| Tool | Purpose |
|------|---------|
| `ctx_execute` | Run a shell/JS/TS/Python/etc. command in a sandboxed subprocess; only stdout enters context. Pass `intent=` to switch to search-mode return when output > 5KB. |
| `ctx_execute_file` | Like `ctx_execute` but takes a file path; the raw file stays in the sandbox while only your computed result returns. **Preferred over `ctx_execute` for shell scripts longer than ~50 lines** or scripts with significant inline content that would inflate the `ctx_execute` `code` payload. Also use whenever the input is a file you would otherwise Read just to feed into analysis. **Access convention (probed v1.0.166, JS): the file content is NOT injected — no `input`/`content` variable, no argv entry, no env var.** The script must open the SAME path you passed (e.g. `fs.readFileSync('<abs path>', 'utf8')`); sandbox cwd is the project root, and paths OUTSIDE the project root are blocked ("File access blocked" — needs a host allow rule). **Text inputs only** — it UTF-8-decodes the file into the script, so it throws on binary. To inspect a binary asset's internals (GLB/glTF, PNG, font, mesh headers), use `ctx_execute` with code that opens the file in binary mode (`rb`) and parses the bytes itself — don't reach for `ctx_execute_file` or launch the engine to dump it. |
| `ctx_index` | Index arbitrary text (notes, pasted snippets) under a descriptive source label so it becomes searchable via `ctx_search`. |
| `ctx_search` | Full-text search across everything indexed this session; always try this before re-running a command. Accepts a `project:` parameter: omit = current session's project; `"global"` = all indexed projects; `"<absolute-path>"` = a specific project. Pass `sort: "timeline"` for chronological recall across prior sessions and auto-memory (useful after `/compact` or `--resume`). Pass `contentType: "code"` or `contentType: "prose"` to filter results by content shape. |
| `ctx_fetch_and_index` | Fetch a URL and index the response body; preferred over `ctx_execute("curl ...")` because it caches and indexes for free. |
| `ctx_batch_execute` | Run several commands in one call and search the combined output. The top-level `queries` array is REQUIRED (the call is rejected without it — pass the questions you want answered from the output). Each command requires a `label` field (used as the FTS5 chunk title — promote from "descriptive" to "required"). Pass `concurrency: 1-8` (default 1) for I/O-bound parallelism (gh API, curl, multi-repo git reads); keep `concurrency: 1` for CPU-bound or stateful commands (npm test, build, lint, port-bound servers). |
| `ctx_stats` | Report what is currently indexed (sources, token counts, age) plus lifetime token-savings dashboard. |
| `ctx_doctor` | Run server-side diagnostics; use when hooks or FTS5 seem broken. Returns a markdown checklist with PASS/FAIL per check. |
| `ctx_upgrade` | Upgrade context-mode in-place. Operator use only — agents should not call this spontaneously without user direction. |
| `ctx_purge` | **Destructive: clears all indexed session content.** Agents must NOT suggest this casually; reserved for explicit user-initiated cleanup. Same caution category as `rm -rf` — irreversible, never invoke without an explicit user request. |
| `ctx_insight` | Open the analytics dashboard in a browser; not useful in non-interactive sessions. |

### Retrieval protocol

Before `ctx_execute` on a topic you have already investigated this session, try `ctx_search(queries=["<topic>"])` first. If it returns useful hits, read them instead of re-running the command. Aim for roughly one `ctx_search` per 3-5 indexing operations (`ctx_execute` with intent / `ctx_index` / `ctx_fetch_and_index`) — if you have indexed five things without searching once, you are almost certainly re-fetching something you already have. For a prior plain Bash/Read/Grep call, `ctx_search` finds only its event summary (command, outcome, file path), not its output — re-run it through `ctx_batch_execute` if you need the content.

Use `ctx_execute_file` when the input is a file path and you want the raw file to stay out of context — pass the file path plus the analysis code, and only your computed result returns.

### Prefer

Prefer `ctx_fetch_and_index` over `ctx_execute("curl ...")` for URL fetches — you get caching and indexing for free. When spawning in an established session, call `ctx_stats` first to see what is already indexed before deciding what to fetch or run.

### Anti-patterns

- **Never use output-truncation primitives (`head -N`, `tail -N`, `sed -n '1,Np'`, `grep -m N`, `awk 'NR<=N'`) to cap large command output in captured shell commands.** Inside `ctx_batch_execute` or an `intent` call, bytes cut before indexing are lost and unsearchable; in plain Bash they are simply gone (nothing indexes Bash output). Exception: bare `head file` / `tail file` (reading a small file directly) is fine; only the `cmd | head -N` pipe-to-truncate pattern is the anti-pattern. When `intent=` is set and output exceeds ~5 KB, `ctx_execute` switches to FTS5 search-mode return with no context-size penalty; below that threshold raw output enters context — write programmatic analysis either way: filter, count, and extract in code, then print only the findings. This rule applies equally to `ctx_execute`, `ctx_execute_file`, and `ctx_batch_execute`. It is advisory — no PreToolUse hook enforces it on sandbox payloads; compliance depends on the agent following the guideline.
