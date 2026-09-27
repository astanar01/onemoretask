---
name: commit-review
description: Cheap single-pass review of commits that were just made (or uncommitted changes) — finds real bugs, edge cases, regressions and untested paths, verifies each one against the code, and reports without changing files. Use when asked to "review the commit", "review what was just done", "check this change for bugs", or when the task board's Review code button starts a reviewer. Args "[low|medium|high] [<sha> ... | <a>..<b>]". A budget alternative to /code-review (no subagents, no multi-pass fan-out).
---

# Commit Review

Based on qa-review (read-only devil's advocate), adapted for local commits instead of a PR,
and built to stay cheap: ONE agent, ONE pass, no subagents, no Workflow, no /code-review.
The cost limit is part of the job — do not "be thorough" past it.

**What costs money is the number of TURNS, not the number of tool calls.** Every turn (each
time you call tools and wait for results) re-reads the whole session context, often 50k+
tokens, before the diff is even counted. Measured: an 11-turn review cost ~730k input tokens
of which the diff was a small part. So: put every independent read into ONE turn (several
tool calls in the same message, or one Bash command that prints several things), and never
spend a turn on something you could have fetched alongside the previous one.

You are a read-only reviewer. Do NOT modify files, stage, commit, or push. Report only.
(If a later message asks you to fix findings, that is a new job: fix, verify, commit, never push.)

## 1. Target

From the args:

- **Level**: `low`, `medium` (default) or `high`. Sets the budget in step 3.
- **Commits**: one or more shas, or a range `a..b`. None given → the last commit (`HEAD`);
  if the working tree has uncommitted changes and no commit was named, review those instead
  (`git diff HEAD`).

If you were given a list of candidate commits and a report of what the task did (the task
board does this, because several sessions commit to the same branch), keep only the commits
that match the report. `git show --stat <sha>` settles doubtful ones. Never review commits
from other work.

## 2. Read the change — diff first, in ONE turn

1. One Bash call gets the narrative and the diff together:
   `git show --stat --format='%h %s%n%b' <shas>; git show --format= -U8 <shas>`
   (or `git diff -U8 <a>..<b>` for a range). `-U8` gives enough surrounding lines that most
   questions answer themselves without opening files. Exclude noise with pathspecs, e.g.
   `-- . ':!*.lock' ':!*.min.js' ':!*.png'`. When picking commits from a candidate list, run
   `git show --stat` on all candidates in this same call.
2. Read the diff and write down EVERY question it leaves open (a caller of a changed
   signature, the rest of a changed function, where a value comes from, whether an old name
   is still used). Then answer them all in ONE next turn: parallel Read (offset/limit, just
   the region) / Grep calls, or one Bash with several `grep -n` / `sed -n 'a,bp'`. Use the
   project's code-navigation tools (search_graph / get_code_snippet / search_code) where it
   has them.
3. Skip generated files, lockfiles, vendored code, binary assets and pure formatting churn.

## 3. Hunt — devil's advocate

Ask of each changed piece: what input, state, order of events or environment breaks it?
Look for:

- Wrong logic: off-by-one, inverted condition, wrong variable, wrong default, missed `return`.
- Edge cases: empty / None / missing key, first run vs later runs, repeated calls, a second
  click, unicode, huge input, concurrent writers.
- Regressions: callers or other paths that relied on the old behavior; renamed or removed
  things still referenced elsewhere (grep the old name).
- Error paths: exceptions swallowed, errors shown to nobody, partial writes, resources not freed.
- Stale state: caches, docs, comments or config that now say something false.
- Untested paths: behavior the change adds that nothing exercises.
- Security only where the change touches input from outside (paths, shell, HTML, SQL, auth).

Do NOT prescribe a checklist up front and do NOT report style, naming or "could be cleaner"
opinions. Only things that can go wrong.

**Budget by level** — counted in TURNS after the diff turn (each turn may hold many
parallel calls), not counting the final report post:

| Level  | Extra turns | Checks you may run                                              |
|--------|-------------|-----------------------------------------------------------------|
| low    | 1           | none — reason from the diff and one batch of reads              |
| medium | 2           | quick ones only, batched with the reads: compile/lint, a tiny script (seconds) |
| high   | 4           | the relevant existing tests, a small repro script               |

Never start the app, a browser, a build farm or long test suites. When the budget runs out,
stop and report what you have.

## 4. Verify before reporting

For every candidate finding, re-read the exact code lines and walk the failure case through
them. Then mark it:

- **Confirmed** — you traced it through the code (or reproduced it with a check).
- **Hypothetical** — plausible but depends on something you could not see; say what.

Drop anything you cannot tie to a concrete input or state. Fewer real findings beat many
guesses. If nothing survives, say so plainly — that is a valid result.

## 5. Report

Most severe first. For each finding:

- **Severity**: critical (data loss, security, crash on a main path) / major (wrong result a
  user will hit) / minor (rare edge case, misleading text).
- **Where**: `file:line`.
- **Problem**: one sentence.
- **Breaks when**: the concrete input or state, and what goes wrong.
- **Fix**: the change you suggest, in a sentence or a tiny snippet.
- **Confirmed / Hypothetical**.

Then one line: which commits you reviewed, the level, and what you did not check (e.g.
"did not run the tests"). End by asking which findings to fix.

Write for someone reading on a small card: short lines, `- ` bullets, plain words. If you
were told to post the report somewhere (the task board's report.py), post the full report
there — chat output alone is not seen.

## Pre-flight before posting

- [ ] No files changed (`git status` shows nothing new from you).
- [ ] Every finding has file:line, a concrete "breaks when", and a Confirmed/Hypothetical mark.
- [ ] No style-only or taste findings.
- [ ] Stayed inside the level's turn budget (independent reads batched into one turn);
      said what was not checked.
