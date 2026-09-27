"""Start the task board server, then open it in the browser. If it is already running, just open it.

Usage: python3 launch.py [server.py options]   (e.g. --port 9000, --project PATH)
bin/onemoretask (macOS/Linux) and bin\\onemoretask.cmd (Windows) run this.
Ctrl-C stops the server.
"""
import os
import runpy
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.realpath(__file__))
SERVER = os.path.join(HERE, "server.py")
# No proxy: a proxy set in the environment must not answer for 127.0.0.1.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def port_from(args):
    port = "8765"
    for i, a in enumerate(args):
        if a.startswith("--port="):
            port = a[len("--port="):]
        elif a == "--port" and i + 1 < len(args):
            port = args[i + 1]
    return port


def answers(url):
    try:
        OPENER.open(url + "/", timeout=1).close()
        return True
    except urllib.error.HTTPError:
        return True  # it answered, just not with 200
    except Exception:
        return False


def open_when_up(url):
    for _ in range(50):
        if answers(url):
            webbrowser.open(url)
            return
        time.sleep(0.2)


def main(args):
    if "-h" in args or "--help" in args:
        sys.argv = [SERVER] + args
        runpy.run_path(SERVER, run_name="__main__")
        return
    url = "http://127.0.0.1:" + port_from(args)
    if answers(url):
        print("Task board already running: " + url)
        webbrowser.open(url)
        return
    threading.Thread(target=open_when_up, args=(url,), daemon=True).start()
    # Run server.py in this process so Ctrl-C and its exit code behave as if it was started directly.
    sys.argv = [SERVER] + args
    sys.path.insert(0, HERE)
    runpy.run_path(SERVER, run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1:])
