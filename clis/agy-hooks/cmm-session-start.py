#!/usr/bin/env python3
"""Antigravity SessionStart hook installed by the task board's setup: reminds the agent how to explore code.
Antigravity passes the system message on to the agent in print mode too."""
import json
import sys

MESSAGE = (
    "The code map is on: before exploring source code, index the repository with the codebase-memory-mcp tool "
    "index_repository, then prefer its search_graph, get_code_snippet, trace_path and search_code over reading whole "
    "files. Use context-mode (ctx_batch_execute, ctx_execute_file, ctx_search) for big outputs. Recall Muninn first "
    "(muninn_recall) and store what you learn at the end (muninn_remember)."
)


def main():
    try:
        json.load(sys.stdin)
    except Exception:
        pass
    print(json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": MESSAGE}}]}))


if __name__ == "__main__":
    main()
