---
name: commit-review
description: Cheap single-pass review of commits that were just made (or uncommitted changes) — finds real bugs, edge cases, regressions and untested paths, runs a small test on each one to prove it (or states its confidence when no test can run), and reports without changing files. Use when asked to "review the commit", "review what was just done", "check this change for bugs", or when the task board's Review code button starts a reviewer. Args "[low|medium|high] [<sha> ... | <a>..<b>]". A budget alternative to /code-review (no subagents, no multi-pass fan-out).
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
parallel calls), not counting the final report post. Every level also gets the test turn(s)
of step 4 — testing the findings is not optional at any level:

| Level  | Read turns | Test turns (step 4) | What the tests may be                                  |
|--------|------------|---------------------|--------------------------------------------------------|
| low    | 1          | 1                   | tiny repro scripts only (seconds each)                 |
| medium | 2          | 1                   | repro scripts, compile/lint                            |
| high   | 3          | 2                   | repro scripts, plus the related existing tests         |

Never start the app, a browser, a build farm or long test suites. When the budget runs out,
stop and report what you have.

## 4. Test every finding before reporting

A finding is a hypothesis until a test says otherwise. Do not report "this might break" —
prove it or measure how sure you are.

1. For EACH candidate finding, write the smallest test that shows the failure: a script
   that imports / calls the changed code with the breaking input, a shell repro on a temp
   copy, a `grep` that proves an old name is still referenced, or the one existing test that
   covers the path. Put scratch files in a temp dir (`$CLAUDE_JOB_DIR/tmp` if set, else
   `mktemp -d`), never in the repo.
2. Run ALL the repro tests in ONE turn (one Bash call that runs each and prints a labeled
   result, or parallel calls). A test that needs a fix to the test itself gets the second
   test turn at high only.
3. Mark each finding by what the test showed:
   - **Reproduced** — the test ran and showed the failure. Quote the key output line.
   - **Refuted** — the test ran and the code behaved. Drop the finding (list it in one line
     under "checked and fine" so the reader knows it was looked at).
   - **Not reproduced — confidence high / medium / low** — you could not run a test (say
     exactly why: needs the running app, a browser, network, real user data, timing). Give
     the confidence and what it rests on (e.g. "high: traced every line, the only unknown is
     X"). A reader should be able to decide from this line whether to trust it.

Never use the word "hypothetical" as a mark. Drop anything you cannot tie to a concrete
input or state. Fewer real findings beat many guesses. If nothing survives, say so
plainly — that is a valid result.

## 5. Report

Most severe first. For each finding:

- **Severity**: critical (data loss, security, crash on a main path) / major (wrong result a
  user will hit) / minor (rare edge case, misleading text).
- **Where**: `file:line`.
- **Problem**: one sentence.
- **Breaks when**: the concrete input or state, and what goes wrong.
- **Fix**: the change you suggest, in a sentence or a tiny snippet.
- **Proof**: Reproduced (quote the test output) / Not reproduced — confidence high, medium
  or low, and why no test could run.

Then: one line listing refuted findings ("checked and fine"), and one line with which
commits you reviewed, the level, and what you did not check. End by asking which findings
to fix.

Write for someone reading on a small card: short lines, `- ` bullets, plain words. If you
were told to post the report somewhere (the task board's report.py), post the full report
there — chat output alone is not seen.

## Pre-flight before posting

- [ ] No files changed (`git status` shows nothing new from you; scratch tests live outside the repo).
- [ ] Every finding has file:line, a concrete "breaks when", and a Proof line: Reproduced with
      quoted output, or Not reproduced with a confidence level and the reason no test ran.
- [ ] Every candidate finding had a test run on it, or a stated reason it could not.
- [ ] No style-only or taste findings.
- [ ] Stayed inside the level's turn budget (independent reads batched into one turn);
      said what was not checked.
