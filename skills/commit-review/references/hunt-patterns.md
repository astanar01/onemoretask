# Hunt patterns that fooled earlier reviews

Each came from a real review that missed it. Read the one whose shape the diff has.

## Text matched across two sources

When code matches one text source against another (a CLI state file vs a rendered screen,
a log line vs a UI label): was it tested past each source's size cap (a field cut at N chars
with "…", wrapped and multi-line screen text), with a second dialog appearing right after
the first, and with a mismatch? "A dialog is present" is not "the same dialog is present".

## Branches that can never fire

For each branch that offers an action (a button, a next step), check its gate can be true
in that state. A helper borrowed from another state brings that state's rules (e.g. a "Done"
branch gated on a check that needs "Review" never fires). Cheap proof: run the pure function
on real data for each state.

## Fixtures cut from real logs or transcripts

Grep them for other projects' paths, task titles and prompt text. Keep only the fields the
code reads; the rest becomes placeholders.
