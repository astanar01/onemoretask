## Fixed both findings

Commit `b91c2e4` on branch `enforce-response-language-rule-mv9h3sqd14jwy`. Both review findings are closed and the 13 checks pass.

### What changed

- **Finding 1 (quoted text):** the guess now reads only the user's own words. Quoted lines, indented blocks and anything after a `:` are skipped.
- **Finding 2 (board handback line):** entries the board wrote are skipped before guessing, so a French user who sends findings back keeps a French answer.
- **Refusal text:** it no longer orders a switch. It says which language the card is in and how to override with `--lang`.

### Before and after

| Case | Before | After |
|---|---|---|
| English note quoting French | refused as French | passes |
| French user sends findings back | check turned off | French enforced |
| `--lang English` on a French card | passes | passes |

### How to test it yourself

1. Open a card whose notes are in French.
2. Reply in English without `--lang`.
3. The report is refused and the card shows why.

```bash
python3 report.py --board .task_board mv9h3s done "Terminé" --lang French
python3 -m pytest tests/test_report_language.py -q
```

> The chat window is still not checked, only the card. That is where you read, so that is where it matters.

**What's left:** nothing on this card. The `--lang` flag could get a short note in the README.
