This folder holds the Claude Code hooks, rules and hook registrations that `setup/install_hooks.py` copies into `~/.claude`; they steer sessions toward codebase-memory-mcp, context-mode and MuninnDB.
The hook scripts are adapted from the hook set shipped with "vibe-better-with-claude-code" by Tiago Serôdio (MIT, https://github.com/yidakee/vibe-better-with-claude-code-vbw), and tuned since.
codebase-memory-mcp is MIT (https://github.com/DeusData/codebase-memory-mcp).
MuninnDB is BSL 1.1 (https://github.com/scrypster/muninndb); context-mode is Elastic-2.0 (https://github.com/mksglu/context-mode).
The installer downloads those three tools from their own sources; this repo does not redistribute them.
