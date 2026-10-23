---
name: commit-review
description: Cheap single-pass review of commits that were just made (or uncommitted changes) — finds real bugs, edge cases, regressions and untested paths, runs a small test on each one to prove it (or states its confidence when no test can run), and reports without changing files. Use when asked to "review the commit", "review what was just done", "check this change for bugs", or when the task board's Review code button starts a reviewer. Args "[low|medium|high] [<sha> ... | <a>..<b>]". A budget alternative to /code-review (no subagents, no multi-pass fan-out).
---

# Commit Review

Read-only devil's advocate for local commits, built to stay cheap: ONE agent, ONE pass,
no subagents, no Workflow, no /code-review. The cost limit is part of the job.

**What costs money is the number of TURNS, not tool calls.** Every turn re-reads the
whole session context (often 50k+ tokens; an 11-turn review cost ~730k input tokens).
Put every independent read into ONE turn (several tool calls in one message, or one
Bash command that prints several things).

Do NOT modify files, stage, commit, or push. Report only. (If a later message asks you
to fix findings, that is a new job: fix, verify, commit, never push unless told.)

## 1. Target

From the args:

- **Level**: `low`, `medium` (default) or `high`. Sets the budget in step 3.
- **Commits**: one or more shas, or a range `a..b`. None given → the last commit
  (`HEAD`); if the working tree has uncommitted changes and no commit was named, review
  those instead (`git diff HEAD`).

Given a list of candidate commits and a report of what the task did (the task board
does this, since several sessions commit to the same branch), keep only the commits
that match the report. `git show --stat <sha>` settles doubtful ones. Never review
commits from other work.

## 2. Read the change — diff first, in ONE turn

1. One Bash call gets narrative and diff together:
   `git show --stat --format='%h %s%n%b' <shas>; git show --format= -U8 <shas>`
   (or `git diff -U8 <a>..<b>`). `-U8` answers most questions without opening files.
   Exclude noise: `-- . ':!*.lock' ':!*.min.js' ':!*.png'`. With a candidate list, run
   `git show --stat` on all candidates in this same call. In the same call, `cat` the
   project's rule files if present (`CLAUDE.md`, `CLAUDE.local.md` at the root and in
   the changed folders): a change that breaks an explicit rule there is a finding; the
   rest is guidance for writing code, not a review checklist.
2. Write down EVERY question the diff leaves open (a caller of a changed signature, the
   rest of a changed function, where a value comes from, whether an old name is still
   used). Answer them all in ONE next turn: parallel Read (offset/limit) / Grep calls, or
   one Bash with several `grep -n` / `sed -n 'a,bp'`. Use the project's code-navigation
   tools (search_graph / get_code_snippet / search_code) where it has them.
3. Given task notes and the worker's report, compare both with the diff. A part of the
   ask the diff does not do, or that the report claims and the diff does not show, is a
   finding (major) — the cheapest bug to find and the one a diff-only review misses.
4. Skip generated files, lockfiles, vendored code, binary assets, pure formatting churn.
5. Change in another folder (a worktree): never a bare `cd` — `git -C <dir>` for git and
   a subshell `( cd <dir> && … )` for the rest. Some environments block a persistent
   `cd`, and a blocked command costs a whole turn.

## 3. Hunt — devil's advocate

Ask of each changed piece: what input, state, order of events or environment breaks it?

- Wrong logic: off-by-one, inverted condition, wrong variable, wrong default, missed `return`.
- Edge cases: empty / None / missing key, first run vs later runs, repeated calls, a
  second click, unicode, huge input, concurrent writers.
- Regressions: callers that relied on the old behavior; renamed or removed things still
  referenced elsewhere (grep the old name).
- Error paths: exceptions swallowed, errors shown to nobody, partial writes, resources not freed.
- Stale state: caches, docs, comments or config that now say something false.
- Untested paths: behavior the change adds that nothing exercises.
- Security only where the change touches outside input (paths, shell, HTML, SQL, auth).
- Patterns that fooled earlier reviews (text matched across two sources, branches that
  can never fire, fixtures cut from real logs): `references/hunt-patterns.md`, when the
  diff has one of those shapes.

No style, naming or "could be cleaner" opinions. Only things that can go wrong.

**Pre-existing bugs.** A bug the diff did not introduce (`git blame` or
`git show <sha>^:<file>` settles it) is not a Finding: one line after the findings
("Pre-existing, not from this change: …"), unless the commit made it worse or built on
it; then it is a Finding that says so.

**A second review of the same work.** If the card already holds a review (an earlier
report with `Finding N` lines), start with one line per old finding — fixed, still open,
or now moot — and number only NEW findings. Do not re-prove what was already proved.

**Budget by level** — in TURNS after the diff turn (each turn may hold many parallel
calls), not counting the final report post. Every level gets the test turn(s) of step 4;
testing the findings is not optional:

| Level  | Read turns | Test turns (step 4) | What the tests may be                          |
|--------|------------|---------------------|------------------------------------------------|
| low    | 1          | 1                   | tiny repro scripts only (seconds each)         |
| medium | 2          | 1                   | repro scripts, compile/lint                    |
| high   | 3          | 2                   | repro scripts, plus the related existing tests |

Never start the app, a browser, a build farm or long test suites. When the budget runs
out, stop and report what you have.

## 4. Test every finding before reporting

A finding is a hypothesis until a test says otherwise.

1. For EACH candidate finding, write the smallest test that shows the failure: a script
   that imports / calls the changed code with the breaking input, a shell repro on a
   temp copy, a `grep` proving an old name is still referenced, or the one existing test
   covering the path. **Call the REAL functions under test** — import them and fake only
   the outside edges (network, clock, files); a repro that copies the code inline keeps
   passing the old logic after the fix. Scratch files go in a temp dir
   (`$CLAUDE_JOB_DIR/tmp` if set, else `mktemp -d`), never in the repo. Git finds the
   repo by walking UP: a scratch folder inside a checkout IS that checkout until it has
   its own `.git`. Test code that touches git in a scratch repo that ran `git init`
   itself, and assert `git rev-parse --show-toplevel` is the scratch path first — or it
   creates branches and worktrees in the real repo.
   **Reviewed commits older than the checkout's HEAD:** never check their files out into
   the checkout under review. Run the repro against HEAD and say so, or get the old code
   in a throwaway `git worktree add --detach <tmp> <sha>` and remove it after. A test
   file outside the repo runs fine as `vendor/bin/phpunit /abs/path/ReproTest.php`.
2. Run ALL repro tests in ONE turn. A test that needs a fix to itself gets the second
   test turn at high only.
3. Mark each finding by what the test showed:
   - **Reproduced** — the test ran and showed the failure. Quote the key output line.
   - **Refuted** — the test ran the breaking input and the code behaved. Drop it (one
     line under "checked and fine").
   - **Not reproduced — confidence high / medium / low** — no test could run, or it ran
     but cannot trigger the failure (a race, timing, real data — NOT Refuted). Say why:
     needs the running app, a browser, network, real user data, timing. Give the
     confidence and what it rests on ("high: traced every line, the only unknown is X").
     **Arithmetic or geometry is not a test of an API.** When the case rests on a
     calculation ("N tries × step × cos(pitch) can't clear a 3.4 m gap"), the sum only
     carries forward a premise about what the engine or API returns next ("the
     closest-point query keeps returning the same island on each retry"). Name it as
     `rests on: <assumption>`. Untested premise → confidence low (such a finding was
     refuted the moment someone measured the premise, after fix work had started).

Never use the word "hypothetical" as a mark. Drop anything not tied to a concrete input
or state. Fewer real findings beat many guesses. If nothing survives, say so plainly.

## 5. Report

Most severe first. Severity: critical (data loss, security, crash on a main path) /
major (wrong result a user will hit) / minor (rare edge case, misleading text).

Each finding in exactly this shape — the task board styles it and only finds lines that
start this way:

```
Finding 1 (major): short title

- Where: `file:line`
- Problem: one sentence.
- Breaks when: the concrete input or state, and what goes wrong.
- Fix: the change you suggest, in a sentence or a tiny snippet.
- Proof: Reproduced (quote the test output) / Not reproduced — confidence high, medium
  or low, and why no test could run; rests on: <the assumption the case depends on>.
```

Then: one line listing refuted findings ("checked and fine"), and one line with which
commits you reviewed, the level, and what you did not check. End by asking which
findings to fix.

Write for a small card: short lines, `- ` bullets, plain words, a blank line between
every block (each finding, the refuted line, the scope line) — never one run-on
paragraph. If told to post the report somewhere (the task board's report.py), post the
full report there — chat output alone is not seen.

## Pre-flight before posting

- [ ] No files changed (`git status` shows nothing new from you; scratch tests live outside the repo).
- [ ] Every finding opens with a `Finding N (severity): title` line and uses the `- Where:` /
      `- Proof:` labels.
- [ ] Every finding has file:line, a concrete "breaks when", and a Proof line: Reproduced with
      quoted output, or Not reproduced with a confidence level and the reason no test ran.
- [ ] Every arithmetic/geometry case names its `rests on:` premise; untested premise → confidence low.
- [ ] Every candidate finding had a test run on it, or a stated reason it could not.
- [ ] No style-only or taste findings. Pre-existing bugs are one line, not a Finding.
- [ ] Task notes and the worker's report were compared with the diff (when given).
- [ ] An earlier review on the card: each old finding has its one-line status; only new ones are numbered.
- [ ] Blocks separated by blank lines; no run-on paragraph anywhere in the report.
- [ ] Stayed inside the level's turn budget (independent reads batched into one turn);
      said what was not checked.
