## Code Navigation

Use codebase-memory-mcp tools as the primary method for code exploration.

### CMM Tool Decision Table

| Question / Intent | Use |
|-------------------|-----|
| Unfamiliar area / package map | `get_architecture` |
| Find by name (function, class, module) | `search_graph` |
| Source of a specific symbol | `get_code_snippet` — always pass `project` (same value as search_graph); the qualified-name prefix does not pick it, and a call without it fails with "project not found" |
| Who calls X / what does X call | `trace_path` |
| Cross-service or graph-wide queries (Cypher) | `query_graph` |
| Text search in code (string literals, error msgs, TODOs) | `search_code` |

Orient first: `get_architecture` → `search_graph` → `get_code_snippet`. Do not jump straight to reading files.

### CMM gate & `# cmm-exempt`

The `cmm-grep-nudge.sh` PreToolUse gate blocks nav-verb Bash (`grep`/`rg`/`find`/`cat`/`head`/`tail`/`wc`) on source-ish paths. The **authoritative policy** is the cmm-rules skill's "When the CMM gate fires" section; summary: CMM tools are mandatory for actual code search/reading — never exempt those. `# cmm-exempt` is legitimate ONLY for (1) filtering a launched binary's output (e.g. `godot --headless … | grep`), (2) non-source state files (markdown, logs, git state, `.import` artifacts), (3) sessions with no ctx_* tools registered. The hook ignores the marker when a nav verb's own arguments name a file CMM indexes (`.py`, `.gd`, `.js`, `.ts`, ...). **Marker placement: LAST thing on the command line** — everything after `#` is a shell comment, so `cmd1 # cmm-exempt; cmd2` silently never runs cmd2 (and the output looks complete); when chaining, marker at the very end only, or split invocations.

### Behavior notes (upstream v0.8.1)

- `search_graph` returns at most **200 rows** by default (upstream cap as of v0.8.1); pass `offset` to page or use `query_graph` with Cypher for full scans.
- `search_code` auto-converts multi-word input to a regex (`foo bar` → `foo.*bar`); pass an explicit single-token regex if you need different semantics.
- `list_projects` now returns all registered projects, including those with `/tmp/`-rooted paths (prior versions filtered tmp paths silently). Useful when triaging multiple indexed scratch projects during QA.
- `trace_path` falls back to a `qualified_name` lookup when the bare `function_name` does not resolve — pass the fully-qualified name (e.g. `module.ClassName.method`) if a bare name returns no results.
- As of v0.8.1, the call graph is LSP-resolved (accurate): `trace_path` and `search_graph` `CALLS` edges resolve to the right callee via hybrid LSP across eight language families (Python, TS/JS/JSX/TSX, PHP, C#, Go, C/C++/CUDA, Java, Kotlin). Languages outside this set (e.g. Rust, Ruby) are NOT cross-file LSP-resolved in v0.8.1 — `CALLS` edges there fall back to heuristics, so prefer `search_code`/grep for call-chain analysis on them. Prefer the graph tools over manual grep for the resolved families.
- **GDScript IS indexed — use CMM on `.gd` like any other language** (measured 2026-10-01 on a Godot game: 5093 `.gd` functions with signature, docstring, callers). Gaps: a `class_name` gets no node (find its file with `search_code "class_name X"` mode `files`, then `search_graph file_pattern`), and GDScript CALLS edges are heuristic. Inline JS inside `.html` is not parsed into functions — grep/ctx is fine there. Probe before claiming a language is unindexed: `query_graph` count of `Function` nodes by file extension.
- **`index_repository` `mode="fast"`/`"moderate"` skip whole dirs (on one game repo: `scripts/`, `tools/`, `docs/`, `assets/`) and still report `indexed`.** Call `index_repository` with no `mode`; `cmm-index-mode-guard.sh` refuses fast/moderate. A graph built in those modes keeps its gaps through later incremental runs: `delete_project`, then index again. Read the result's `excluded` list; 0 hits for a symbol you know exists means a coverage gap, not absence. Details: cmm-rules skill, Behavior notes.
- As of v0.8.1, `query_graph` exposes per-function complexity metrics queryable via Cypher: `cyclomatic`, `cognitive`, `param_count`, `loop_depth`, `transitive_loop_depth` (interprocedural worst-case nesting), `linear_scan_in_loop` (O(n²) detection), `alloc_in_loop`, `unguarded_recursion`, and the `recursive` flag. Use these for refactor-candidate and hot-path queries — e.g. `MATCH (f:Function) WHERE f.transitive_loop_depth >= 3 OR f.linear_scan_in_loop >= 1 RETURN f.qualified_name ORDER BY f.transitive_loop_depth DESC`.

`Read` is correct for: non-code files (JSON, YAML, config, Markdown), full-file context (imports, globals), files under 50 lines, and files not yet indexed.

### CMM vs. context-mode

CMM indexes code symbols across sessions (persistent graph of functions/classes/modules). Context-mode captures tool output within one session (FTS5 search over Bash/Read/Grep results). Use CMM for "where is this function defined"; use `ctx_search` for "what did my last test run print".
