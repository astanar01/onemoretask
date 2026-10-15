"""Alerts reach open board pages over /api/alerts/stream; there is no other alert (no macOS notification).

Run: python3 tests/test_alert_stream.py  (stdlib only; a fresh temp HOME, a real server on a free port)."""
import http.client
import json
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="alert-stream-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

proj = os.path.join(TMP, "proj")
os.makedirs(proj)
p = server.Project(proj)
os.makedirs(p.board)
published = []
publish = server.ALERTS.publish
server.ALERTS.publish = lambda a: (published.append(a), publish(a))
server.ALERT_PING = 60  # no ping can find a closed page here; (c) lowers it to see a ping
W = server.WATCH
httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
fails = 0


def check(ok, what, detail=""):
    global fails
    fails += not ok
    print("ok  " if ok else "FAIL", what, "" if ok else detail)


def fire(task_id, phase="finished"):
    """Queue an alert the watcher would fire now (no page has read this board) and flush it."""
    W.alerts[(p.id, task_id)] = {"p": p, "id": task_id, "phase": phase, "at": time.time() - 3600, "column": "doing",
                                 "updated": "", "title": "Claude finished", "sub": f"proj: {task_id}",
                                 "reason": "All done."}
    W.flush_alerts()


def connect():
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/api/alerts/stream")
    r = c.getresponse()
    return c, r


def read_event(r, pings=True):
    """The next blank-line-ended block, as its lines (skipping pings unless pings)."""
    lines = []
    while True:
        line = r.fp.readline().decode()
        if not line:
            return lines or None
        if line == "\n":
            if lines == [": ping"] and not pings:
                lines = []
                continue
            return lines
        lines.append(line.rstrip("\n"))


def wait(cond, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# (a) a listening page gets the event.
c, r = connect()
check(r.status == 200 and r.getheader("Content-Type") == "text/event-stream"
      and r.getheader("Cache-Control") == "no-cache", "the stream answers with event-stream headers")
hello = read_event(r)
check(hello is not None and ": hello" in hello, "a hello comes first, so the browser opens the stream", hello)
check(wait(lambda: len(server.ALERTS.subs) == 1), "the page is listed as listening")
fire("t1", "needs_you")
ev = read_event(r, pings=False)
data = json.loads(ev[1][len("data: "):]) if ev and len(ev) == 2 and ev[0] == "event: alert" else None
want = {"project": p.id, "task": "t1", "title": "Claude finished", "sub": "proj: t1", "reason": "All done.",
        "phase": "needs_you"}
check(data is not None and isinstance(data.get("seq"), int) and {k: data[k] for k in want} == want,
      "the alert event carries the card's details", ev)
first_seq = data and data["seq"]
r.close()  # the response holds the socket too
c.close()

# (d) a closed page is noticed within about a second, with no write to it.
time.sleep(1.5)
check(not server.ALERTS.subs, "the closed page is dropped", server.ALERTS.subs)

# (b) no page at all: the alert is still sent at once (a page opened later does not get it).
published.clear()
fire("t3")
check([a["task"] for a in published] == ["t3"] and not server.WATCH.alerts, "no page: the alert is sent, not kept",
      (published, server.WATCH.alerts))

# (c) a page opened later gets the next event, and pings.
server.ALERT_PING = 1
c, r = connect()
read_event(r)
check(wait(lambda: len(server.ALERTS.subs) == 1), "the new page is listed as listening")
fire("t4")
ev = read_event(r, pings=False)
data = json.loads(ev[1][len("data: "):]) if ev and len(ev) == 2 else None
check(data is not None and data["task"] == "t4" and data["seq"] > first_seq, "the new page gets the event", ev)
ev = read_event(r)
check(ev == [": ping"], "a ping keeps the stream alive", ev)

# A foreign page may not listen (it could read the alerts).
c2 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
c2.request("GET", "/api/alerts/stream", headers={"Origin": "http://evil.example"})
check(c2.getresponse().status == 403, "another site's page is refused")
c2.close()

# Shutting the server down ends an open stream.
httpd.shutdown()
httpd.server_close()
try:
    ended = any(read_event(r) is None for _ in range(5))  # a ping may still come first
except OSError:
    ended = False
check(ended and wait(lambda: not server.ALERTS.subs), "closing the server ends the stream")
c.close()
print("PASS" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
