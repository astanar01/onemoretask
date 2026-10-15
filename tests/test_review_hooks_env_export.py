"""install_tools.add_env_line must see `export KEY=…` (any spacing) as the key already set and not append a second
line. Works on a temp env file only."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "setup"))
import install_tools  # noqa: E402

LINE = "MUNINN_OLLAMA_URL=http://localhost:11434"
CASES = [
    ("export MUNINN_OLLAMA_URL=http://x\n", False),
    ("  export   MUNINN_OLLAMA_URL = http://x\n", False),
    ("export\tMUNINN_OLLAMA_URL=http://x\n", False),
    ("MUNINN_OLLAMA_URL=http://x\n", False),
    (" MUNINN_OLLAMA_URL =http://x\n", False),
    ("export OTHER=1\n", True),
    ("export MUNINN_OLLAMA_URL_2=1\n", True),
    ("# MUNINN_OLLAMA_URL=http://x\n", True),
    ("", True),
]


def main():
    tmp = tempfile.mkdtemp()
    try:
        fails = []
        for i, (text, want) in enumerate(CASES):
            path = os.path.join(tmp, f"env{i}")
            Path(path).write_text(text)
            got = install_tools.add_env_line(path, LINE, False)
            lines = Path(path).read_text().splitlines()
            ok = got == want and lines.count(LINE) == (1 if want else 0)
            print(f"{'ok ' if ok else 'FAIL'} appended={got} want={want} :: {text!r}")
            if not ok:
                fails.append(text)
        if fails:
            print("FAILED:", fails)
            return 1
        print("PASS test_review_hooks_env_export")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
