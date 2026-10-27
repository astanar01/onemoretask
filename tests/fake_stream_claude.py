"""A stand-in `claude -p --input-format stream-json` for tests/test_autofill.py: takes 1 s to start, then answers one
question with its pid, the question and its arguments. With no question (stdin closed) it ends, as claude does."""
import json
import os
import sys
import time

time.sleep(1)
line = sys.stdin.readline()
if not line:
    sys.exit(0)
msg = json.loads(line)["message"]["content"]
print(json.dumps({"type": "system", "subtype": "init"}))
print(json.dumps({"type": "result", "result": json.dumps({"pid": os.getpid(), "msg": msg, "argv": sys.argv[1:]})}))
