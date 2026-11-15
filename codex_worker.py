#!/usr/bin/env python3
"""Run one Codex worker for a Claude card session whose subagent model is an OpenAI one.

  python3 codex_worker.py --board <board> <task_id> --model <codex model> --name "<short label>" [--read-only] [--cwd <dir>]

The prompt comes on stdin. Waits for codex, prints the worker's last message; on failure exits non-zero with the end
of codex's stderr. Logs to <board>/agent_reports/codex-workers/ (codex.worker_rows shows them on the card)."""
import argparse
import json
import os
import signal
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codex  # noqa: E402


def write_meta(path, meta):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")  # the board may read it meanwhile
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", required=True)
    ap.add_argument("task")
    ap.add_argument("--model", required=True)
    ap.add_argument("--name", default="Codex worker")
    ap.add_argument("--read-only", action="store_true")
    ap.add_argument("--cwd", default=os.getcwd())
    a = ap.parse_args(argv)
    prompt = sys.stdin.read()
    if not prompt.strip():
        sys.exit("codex_worker: no prompt on stdin")
    cwd = os.path.abspath(a.cwd)
    started = time.time()
    base = os.path.join(codex.worker_folder(a.board), f"{a.task}-{int(started * 1000)}")
    log, err, meta_path = base + ".jsonl", base + ".err", base + ".json"
    args = codex.CODEX_CMD + ["exec", "--json", "--skip-git-repo-check", "-s",
                              "read-only" if a.read_only else "workspace-write", "-m", a.model, "-C", cwd, "-"]
    proc = codex._spawn(args, cwd, prompt, log, err)
    meta = {"task": a.task, "name": a.name, "model": a.model, "cwd": cwd, "log": log, "err": err, "started": started,
            "pid": proc.pid, "readOnly": a.read_only, "exit": None}
    write_meta(meta_path, meta)

    def stop(sig, _frame):  # codex runs in its own session: a killed helper must not leave it working
        codex._kill(proc)
        raise SystemExit(128 + sig)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        code = proc.wait()
    finally:
        meta["exit"] = proc.returncode
        write_meta(meta_path, meta)
    answer = codex.last_answer(log)
    if code != 0:
        if answer:
            print(answer)
        print(codex._detail({"log": log, "err": err}) or f"codex exited {code}", file=sys.stderr)
        return code or 1
    print(answer or "(the worker gave no final message)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
