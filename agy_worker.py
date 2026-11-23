#!/usr/bin/env python3
"""Run one Antigravity worker for a Claude card session whose subagent model is an Antigravity one.

  python3 agy_worker.py --board <board> <task_id> --model agy:<slug> --name "<short label>" [--read-only] [--cwd <dir>]

The prompt comes on stdin. Waits for agy, prints the worker's answer; on failure exits non-zero with what went wrong
(exit 0 from agy alone is not success: the last turn's result line decides). Logs to
<board>/agent_reports/agy-workers/ (agy.worker_rows shows them on the card)."""
import argparse
import importlib.util
import json
import os
import signal
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agy  # noqa: E402
import procs  # noqa: E402
import reports  # noqa: E402

BIND = "bind: operation not permitted"


def setup_step(name, *args):
    """Run a never-failing part of setup/install_clis.py (rules, project skills) before agy starts; skipped when the
    file or the function is missing."""
    path = os.path.join(HERE, "setup", "install_clis.py")
    try:
        spec = importlib.util.spec_from_file_location("install_clis", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        getattr(mod, name)(*args)
    except Exception:  # noqa: BLE001 - a setup problem never stops the worker
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", required=True)
    ap.add_argument("task")
    ap.add_argument("--model", required=True)
    ap.add_argument("--name", default="Antigravity worker")
    ap.add_argument("--read-only", action="store_true")
    ap.add_argument("--cwd", default=None)
    a = ap.parse_args(argv)
    prompt = sys.stdin.read()
    if not prompt.strip():
        sys.exit("agy_worker: no prompt on stdin")
    if not agy.is_model(a.model):
        sys.exit(f"agy_worker: not an Antigravity model: {a.model!r} (expected agy:<name>)")
    board = os.path.abspath(a.board)
    project = getattr(reports, "project_of_board", lambda b: os.path.dirname(b))(board)
    cwd = os.path.abspath(a.cwd or project or os.getcwd())
    setup_step("ensure_rules", "antigravity")
    setup_step("ensure_project_skills", project)
    if os.path.realpath(cwd) != os.path.realpath(project):
        setup_step("ensure_project_skills", project, cwd)
    started = time.time()
    base = os.path.join(agy.worker_folder(board), f"{a.task}-{int(started * 1000)}")
    log, err, meta_path = base + ".jsonl", base + ".err", base + ".json"
    if a.read_only:
        obs = os.path.join(project, "skill-observations")
        work = agy.answer_dir(board, f"{a.task}-w{int(started * 1000)}")
        os.makedirs(work, exist_ok=True)
        st = agy.settings(work, read=[project], write=[board, *([obs] if os.path.isdir(obs) else [])], git=False)
        cwd = work
    else:
        st = agy.settings(cwd)
    args = agy.argv(a.model, json.dumps(st, separators=(",", ":")), base + ".agy.log")
    proc = procs._spawn(args, cwd, agy.stdin_line(prompt), log, err)
    meta = {"task": a.task, "name": a.name, "model": a.model, "cwd": cwd, "log": log, "err": err, "started": started,
            "pid": proc.pid, "pid_start": procs._pid_start(proc.pid), "readOnly": a.read_only, "exit": None}
    procs.write_json(meta_path, meta)

    got = []

    def stop(sig, _frame):  # agy runs in its own session: a killed helper must not leave it working
        got.append(sig)
        procs._signal(proc)
        t = threading.Timer(5, procs._signal, (proc, True))
        t.daemon = True
        t.start()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        code = proc.wait()
    finally:
        meta["exit"] = proc.returncode
        procs.write_json(meta_path, meta)
    if got:
        for _ in range(100):  # the commands agy started are in its group: wait until they are gone
            if not procs.group_alive(proc.pid):
                break
            procs._signal(proc, True)
            time.sleep(0.05)
        return 128 + got[0]
    state, detail = agy.turn_state({**meta, "exit": code})
    answer = agy.last_answer(log)
    if state != "idle":
        if answer:
            print(answer)
        if BIND in agy._from(err, 0):
            detail = "Antigravity cannot start inside this sandbox (it needs a local port)"
        print(detail or f"agy exited {code}", file=sys.stderr)
        return code or 1
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
