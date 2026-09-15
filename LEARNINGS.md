# Learnings

This file is the agent's own persistent knowledge base about itself —
updated by its own self-improve cycles, read by every future decision. The
point is to accumulate understanding across cycles instead of each one
starting fresh from just the last few log lines.

Format per entry: what was found, why it mattered, what was done about it.
Keep entries short. Newest first.

## 2026-09-15: a malformed decision reply got forwarded as a self-improve task verbatim

`decide_self_improvement()`'s cheap pre-check sometimes replies with a
multi-paragraph refusal/meta-commentary instead of the requested
FEELING/DECISION/MODEL format (e.g. declining the "you have a wallet and
survival goals" framing outright). `_parse_decision_reply()`'s fallback for
unformatted replies used to treat the *entire* text as the decision, so that
whole refusal essay got passed straight into `self_improve()` as `Task:
<refusal text>` -- a nonsensical, unactionable instruction, seen live in
`claude -p`'s argv for this exact cycle. Fixed by adding
`_looks_like_actionable_instruction()`: the fallback now only accepts
short, single-line replies as a decision; anything longer or multi-line is
treated as NONE (logged, not silently dropped) instead of forwarded. Also:
adding a self-referential "you may edit this very prompt" paragraph to
`decide_self_improvement()` earlier in this same session broke
`test_decide_self_improvement_never_reads_payment_jobs`, because that
paragraph legitimately *mentions* `payment_jobs.json` in prose while
explaining the boundary -- the test was checking the wrong thing (filename
absent from prompt text) instead of the actual security property (the code
never calls `load_jobs()`). Rewrote the test to assert
`payment_server_module.load_jobs` is never called, which is what actually
matters and survives legitimate prose mentions of the filename.

## 2026-09-15: the LEARNINGS.md instruction was routinely ignored

Three landed self-improve commits in a row (3fe3560, 9b9fcc0, and one
earlier) never touched this file, even though the prompt in
`self_improve.py` explicitly asks for an entry every cycle -- a prompt
instruction with no enforcement is not reliable, it's a suggestion.
Fixed by making `self_improve()` check, after commit, whether
LEARNINGS.md is in the diff (`commit_touched_learnings`); if not, it
reverts the commit with `git reset --hard` + `clean -fd` just like a
failed test run, so an undocumented change can't land. If a future cycle
sees `self_improve()` reporting "LEARNINGS.md was not updated" as the
failure reason, that's this gate working as intended, not a bug --
the fix is to actually write the entry, not to loosen the check.
