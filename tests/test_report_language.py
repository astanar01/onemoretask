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

# Pasted or quoted text in another language is not the user's language.
FR_PARA = ("Le joueur est dans la maison et il ne peut pas sortir. C'est une porte qui est fermée pour les enfants, "
           "mais ce n'est pas très grave pour nous.")
check("English ask with a pasted French paragraph is not French",
      reports.user_language("Please translate this to English:\n\n" + FR_PARA) != "French")
check("French ask with a pasted French paragraph reads as French",
      reports.user_language("Traduis ce texte en anglais pour le menu :\n\n" + FR_PARA) == "French")
check("English bug report after a colon still reads as English", reports.user_language(
    "Here's the bug:\nThe card does not close when it is done and the button is not there. It was there before.")
      == "English")
check("quoted French is left out", reports.user_language(f'Can you translate "{FR_PARA}" for the menu') != "French")
reports.append(BOARD, TASK, "reply", "Please translate this to English:\n\n" + FR_PARA, "you")
code, out = report(EN)
check("English report posted under an English ask with French pasted", code == 0)

# The board's handback line is not the user's words: its typed part counts, the default text never does.
reports.append(BOARD, TASK, "reply", "Peux-tu me répondre en français à partir de maintenant ? Je préfère ça pour "
               "cette tâche, merci.", "you")
reports.append(BOARD, TASK, "reply", reports.HANDBACK.format("Claude") + reports.HANDBACK_ASK, "you")
code, out = report(EN)
check("default handback falls back to the user's French reply", code != 0 and "in French" in out)
reports.append(BOARD, TASK, "reply", reports.HANDBACK.format("Codex") + "Corrige les deux problèmes, mais ne "
               "change pas le menu et garde le bouton dans la carte.", "you")
check("typed handback words count without the board prefix",
      reports.latest_user_text(BOARD, TASK).startswith("Corrige") and report(EN)[0] != 0)

print("PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
