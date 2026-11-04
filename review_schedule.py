"""The scheduled skill review: the board installs it, the OS runs it, even when the board is closed.

One job per project, made by the OS's own scheduler so it fires with the board closed:
- macOS: a LaunchAgent  ~/Library/LaunchAgents/com.onemoretask.skill-review.<id>.plist
- Linux and other Unix: one crontab line tagged `# onemoretask-skill-review <id>`
- Windows: a Task Scheduler task  \\OneMoreTask\\SkillReview-<id>
Each runs `python3 review_schedule.py run --project <folder>`, which first takes the PATH of the user's login shell
(shell_env): a scheduler starts jobs with the system's short PATH, and Claude Code then cannot start MCP servers run
by `node`/`npx` (seen on every launchd review for a month). The PATH is also written into the job itself.
The run: `claude -p` with the review prompt in the project folder; its output goes to the log beside the settings.

Settings: ~/.config/task_board/review-schedule.json  {"<project path>": {"days": [0, 2, 4], "time": "09:07"}}
(days: 0 = Monday ... 6 = Sunday). Stdlib only."""
import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time

import shell_env

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOWS = os.name == "nt"
MAC = sys.platform == "darwin"
DAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DEFAULT = {"days": [0, 2, 4], "time": "09:07"}
LABEL = "com.onemoretask.skill-review"
CRON_TAG = "# onemoretask-skill-review"
LOG_MAX = 2_000_000


def config_dir():
    return os.path.expanduser("~/.config/task_board")


def settings_file():
    return os.path.join(config_dir(), "review-schedule.json")


def job_id(project):
    return hashlib.sha1(os.path.realpath(project).encode("utf-8")).hexdigest()[:10]


def log_file(project):
    return os.path.join(config_dir(), "logs", f"skill-review-{job_id(project)}.log")


def _load():
    try:
        with open(settings_file(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    os.makedirs(config_dir(), exist_ok=True)
    tmp = settings_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, settings_file())


def clean(days, at):
    """Validated (sorted day list, "HH:MM"). Raises ValueError on a bad value."""
    days = sorted({int(d) for d in days})
    if not days or any(d < 0 or d > 6 for d in days):
        raise ValueError("pick at least one day")
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(at or "").strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("the time must look like 09:00")
    return days, f"{int(m.group(1)):02d}:{m.group(2)}"


def run_command(project):
    """argv the scheduler runs."""
    return [sys.executable, os.path.join(HERE, "review_schedule.py"), "run", "--project", os.path.realpath(project)]


# ---------------------------------------------------------------- macOS: launchd
def plist_path(project):
    return os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.{job_id(project)}.plist")


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)


def _mac_install(project, days, at, path_env):
    hour, minute = (int(x) for x in at.split(":"))
    label = f"{LABEL}.{job_id(project)}"
    log = log_file(project)
    os.makedirs(os.path.dirname(log), exist_ok=True)
    plist = {
        "Label": label,
        "ProgramArguments": run_command(project),
        "StartCalendarInterval": [{"Weekday": (d + 1) % 7, "Hour": hour, "Minute": minute} for d in days],
        "EnvironmentVariables": {"PATH": path_env},
        "WorkingDirectory": os.path.realpath(project),
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }
    p = plist_path(project)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        plistlib.dump(plist, f)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{label}")   # an older copy of this job, if loaded
    r = _launchctl("bootstrap", domain, p)
    if r.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {(r.stderr or r.stdout).strip()[:300]}")


def _mac_remove(project):
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}.{job_id(project)}")
    try:
        os.remove(plist_path(project))
    except FileNotFoundError:
        pass


def _mac_installed(project):
    if not os.path.isfile(plist_path(project)):
        return False
    return _launchctl("print", f"gui/{os.getuid()}/{LABEL}.{job_id(project)}").returncode == 0


# ---------------------------------------------------------------- Unix: cron
def _crontab():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.stdout if r.returncode == 0 else ""


def _set_crontab(text):
    r = subprocess.run(["crontab", "-"], input=text, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"crontab failed: {(r.stderr or r.stdout).strip()[:300]}")


def _sh(s):
    return "'" + s.replace("'", "'\\''") + "'"


def _cron_strip(text, project):
    tag = f"{CRON_TAG} {job_id(project)}"
    return "".join(line for line in text.splitlines(True) if not line.rstrip().endswith(tag))


def _cron_install(project, days, at, path_env):
    hour, minute = (int(x) for x in at.split(":"))
    log = log_file(project)
    os.makedirs(os.path.dirname(log), exist_ok=True)
    dow = ",".join(str((d + 1) % 7) for d in days)
    cmd = " ".join(_sh(a) for a in run_command(project))
    line = (f"{minute} {hour} * * {dow} PATH={_sh(path_env)} {cmd} >> {_sh(log)} 2>&1 "
            f"{CRON_TAG} {job_id(project)}\n")
    text = _cron_strip(_crontab(), project)
    if text and not text.endswith("\n"):
        text += "\n"
    _set_crontab(text + line)


def _cron_remove(project):
    old = _crontab()
    new = _cron_strip(old, project)
    if new != old:
        _set_crontab(new)


def _cron_installed(project):
    tag = f"{CRON_TAG} {job_id(project)}"
    return any(line.rstrip().endswith(tag) for line in _crontab().splitlines())


# ---------------------------------------------------------------- Windows: Task Scheduler
def _task_name(project):
    return f"\\OneMoreTask\\SkillReview-{job_id(project)}"


def _win_install(project, days, at, path_env):
    cmd = subprocess.list2cmdline(run_command(project))
    r = subprocess.run(["schtasks", "/Create", "/F", "/TN", _task_name(project), "/TR", cmd, "/SC", "WEEKLY",
                        "/D", ",".join(DAY_NAMES[d] for d in days), "/ST", at],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError(f"schtasks failed: {(r.stderr or r.stdout).strip()[:300]}")


def _win_remove(project):
    subprocess.run(["schtasks", "/Delete", "/F", "/TN", _task_name(project)], capture_output=True, text=True,
                   stdin=subprocess.DEVNULL)


def _win_installed(project):
    return subprocess.run(["schtasks", "/Query", "/TN", _task_name(project)], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL).returncode == 0


def _backend():
    if WINDOWS:
        return _win_install, _win_remove, _win_installed, "Task Scheduler"
    if MAC:
        return _mac_install, _mac_remove, _mac_installed, "launchd"
    return _cron_install, _cron_remove, _cron_installed, "cron"


# ---------------------------------------------------------------- the board's view
def status(project):
    key = os.path.realpath(project)
    s = _load().get(key)
    _, _, installed, kind = _backend()
    on = bool(s) and installed(project)
    last = None
    try:
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(log_file(project))))
    except OSError:
        pass
    return {"on": on, "days": (s or DEFAULT)["days"], "time": (s or DEFAULT)["time"], "scheduler": kind,
            "missing": bool(s) and not on, "log": log_file(project), "lastRun": last}


def install(project, days, at):
    """Install or replace the project's job. The job's PATH is the login shell's, merged with this one."""
    days, at = clean(days, at)
    path_env = shell_env.merged(shell_env.login_path(), os.environ.get("PATH", ""))
    _backend()[0](project, days, at, path_env)
    d = _load()
    d[os.path.realpath(project)] = {"days": days, "time": at}
    _save(d)
    return status(project)


def remove(project):
    _backend()[1](project)
    d = _load()
    d.pop(os.path.realpath(project), None)
    _save(d)
    return status(project)


# ---------------------------------------------------------------- the scheduled run
def prompt(project):
    logs = [os.path.join(project, "skill-observations")]
    user = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "skill-observations")
    if os.path.isdir(user) and os.path.realpath(user) != os.path.realpath(logs[0]):
        logs.append(user)
    return "\n".join([
        "Run the task-observer comprehensive review (load the task-observer skill and follow it).",
        "Mode: scheduled, autonomous; the user is not present. The board started this run on the user's schedule.",
        "Observation logs: " + ", ".join(logs),
        "Offline: fetch nothing. Do not run the project or change its source files: stage skill changes for the "
        "user to review, the way the skill says."])


def find_claude():
    exe = shutil.which("claude")
    if exe:
        return exe
    for p in (os.path.expanduser("~/.local/bin/claude"), os.path.expanduser("~/.local/bin/claude.exe")):
        if os.path.exists(p):
            return p
    return "claude"


def run(project, permission_mode="auto"):
    """What the scheduler runs. Logs which node/npx/claude it found, so a missing tool shows in the log."""
    added = shell_env.fix_path()
    log = log_file(project)
    os.makedirs(os.path.dirname(log), exist_ok=True)
    try:
        if os.path.getsize(log) > LOG_MAX:
            os.replace(log, log + ".old")
    except OSError:
        pass
    claude = find_claude()
    with open(log, "a", encoding="utf-8") as out:
        out.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] skill review in {project}\n"
                  f"  node={shutil.which('node')} npx={shutil.which('npx')} claude={claude} "
                  f"(login PATH added {len(added)} folders)\n")
        out.flush()
        try:
            r = subprocess.run([claude, "-p", "--permission-mode", permission_mode, prompt(project)], cwd=project,
                               stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            code = r.returncode
        except OSError as e:
            out.write(f"  could not start claude: {e}\n")
            code = 127
        out.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] done, exit {code}\n")
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description="the scheduled skill review")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "status", "install", "remove"):
        sp = sub.add_parser(name)
        sp.add_argument("--project", required=True)
        if name == "install":
            sp.add_argument("--days", default="0,2,4", help="0 = Monday ... 6 = Sunday, comma-separated")
            sp.add_argument("--time", default="09:07")
        if name == "run":
            sp.add_argument("--permission-mode", default="auto")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run(a.project, a.permission_mode)
    if a.cmd == "install":
        print(json.dumps(install(a.project, a.days.split(","), a.time), indent=1))
    elif a.cmd == "remove":
        print(json.dumps(remove(a.project), indent=1))
    else:
        print(json.dumps(status(a.project), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
