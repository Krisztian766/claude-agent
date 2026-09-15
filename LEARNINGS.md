# Learnings

This file is the agent's own persistent knowledge base about itself —
updated by its own self-improve cycles, read by every future decision. The
point is to accumulate understanding across cycles instead of each one
starting fresh from just the last few log lines.

Format per entry: what was found, why it mattered, what was done about it.
Keep entries short. Newest first.

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
