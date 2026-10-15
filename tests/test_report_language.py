"""report.py refuses a report in another language than the user's latest message, and lets --lang through.

Run: python3 tests/test_report_language.py  (stdlib only; a throwaway board)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import reports  # noqa: E402

BOARD = tempfile.mkdtemp(prefix="report-lang-")
TASK = "t1"
EN_NOTES = ("We need a way to enforce the language you speak when answering. If the word french is in a task, "
            "you start answering in French. How could we enforce that as a rule?")
EN = "Added a check to the report script. It refuses a report that is not in the language of the user's message."
FR = "J'ai ajouté une vérification dans le script. Il refuse un rapport qui n'est pas dans la langue de l'utilisateur."
fails = 0


def check(name, ok):
    global fails
    print(("ok   " if ok else "FAIL ") + name)
    fails += not ok


def report(message, *extra):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "report.py"), "--board", BOARD, *extra, TASK, "progress",
                        message], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def posted():
    return [e["message"] for e in reports.read(BOARD, TASK) if e["from"] == "claude"]


with open(os.path.join(BOARD, "tasks.json"), "w", encoding="utf-8") as f:
    json.dump({"tasks": [{"id": TASK, "title": "Enforce response language", "notes": EN_NOTES}]}, f)

# The guesser itself.
check("English notes read as English", reports.guess_language(EN_NOTES) == "English")
check("French text reads as French", reports.guess_language(FR) == "French")
check("a short line is not guessed", reports.guess_language("Reading the report script.") is None)
check("French in backticks is ignored",
      reports.guess_language("The button now says `Envoyer à Claude et le fermer` on the card when it is done.")
      == "English")

# Card notes in English: a French report is refused, an English one goes through.
code, out = report(FR)
check("French report refused under English notes", code != 0 and "in French" in out and "in English" in out)
check("refused report not posted", FR not in posted())
code, out = report(EN)
check("English report posted", code == 0 and EN in posted())
code, out = report(FR, "--lang", "French")
check("--lang posts it anyway", code == 0 and FR in posted())

# A French reply on the card flips it: now English is refused.
reports.append(BOARD, TASK, "reply", "Peux-tu me répondre en français à partir de maintenant ? Je préfère ça pour "
               "cette tâche, merci.", "you")
code, out = report(EN)
check("English report refused after a French reply", code != 0 and "in French" in out)
code, out = report(FR)
check("French report posted after a French reply", code == 0)

# A short user message cannot be guessed, so nothing is refused.
reports.append(BOARD, TASK, "reply", "ok go", "you")
code, out = report(FR)
check("unguessable user message never refuses", code == 0)

print("PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
