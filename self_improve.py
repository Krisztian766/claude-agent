"""Self-modification: lets the agent edit its own source code, gated by git
and the test suite so a bad EDIT can never stick.

Flow:
  1. Refuse to run if the working tree isn't clean (never revert the
     owner's own uncommitted work).
  2. Ask Claude (Read/Write/Edit/Grep/Glob/Bash -- see below) to make the
     change.
  3. Run the test suite.
  4. Tests fail  -> `git reset --hard HEAD` + `git clean -fd` (discard
     everything, including any new untracked files), report failure.
     Tests pass  -> `git add -A && git commit`, report the new commit hash.

This is deliberately owner-triggered only (CLI `agent.py self-improve`, or
the owner-configured autonomous.py orchestrator) -- nothing reachable from
payment_server.py can call this; see payment_server.py's own docstring for
that boundary.

SECURITY NOTE on Bash (added 2026-09-15, owner's explicit request, including
Docker access -- "teljes hatalmat... létrehozhat új docker konténereket"):
git reset/clean only undoes changes to files IN THIS REPO. It does NOT undo
anything Bash actually DID while a self-improve run was in progress --
packages installed, containers started, files written elsewhere on the VPS,
network requests already made. A failed test run reverts the CODE, not the
side effects. This is a real, accepted risk, not an oversight -- the owner
was told this explicitly before it was turned on. Two things keep it from
being worse than it sounds: (1) this path is unreachable from
payment_server.py / anonymous strangers -- only the owner's own CLI or the
autonomous orchestrator's own bounded decision loop can trigger it, and (2)
autonomous.py's decide_self_improvement() deliberately never reads
payment_jobs.json (stranger-submitted text), specifically to avoid an
indirect prompt-injection path into this now much more capable tool set --
don't add that file (or any other stranger-influenced input) back into what
feeds a self-improve decision without re-closing that gap.
"""
import subprocess
import sys
from pathlib import Path

from claude_client import invoke_claude

BASE_DIR = Path(__file__).resolve().parent
SELF_IMPROVE_ALLOWED_TOOLS = "Read Write Edit Grep Glob Bash"


def git(*args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd or BASE_DIR, capture_output=True, text=True)


def working_tree_clean(cwd=None) -> bool:
    r = git("status", "--porcelain", cwd=cwd)
    return r.stdout.strip() == ""


def run_tests(cwd=None) -> subprocess.CompletedProcess:
    cwd = cwd or BASE_DIR
    venv_python = cwd / "venv" / "bin" / "python3"
    python = str(venv_python) if venv_python.exists() else sys.executable
    return subprocess.run([python, "-m", "pytest", "tests/", "-q"], cwd=cwd, capture_output=True, text=True)


def self_improve(instruction: str, cwd=None) -> dict:
    cwd = cwd or BASE_DIR

    if not working_tree_clean(cwd):
        return {"applied": False, "reason": "working tree not clean, refusing to self-improve"}

    prompt = (
        "You are improving your own source code, in this very directory. "
        f"Task: {instruction}\n"
        "Keep all existing behavior working. Add or update tests under tests/ "
        "for whatever you change. Do not touch inbox/, done/, agent.log, "
        "venv/, wallet.json, or replica_registry.json. "
        "Also add a short entry to the TOP of LEARNINGS.md (under the header, "
        "newest-first) describing what you found and fixed and why it "
        "mattered -- 2-4 sentences, no fluff. This is your own persistent "
        "memory across cycles, so make it genuinely useful to your future "
        "self, not a changelog restating the commit message."
    )
    claude_result = invoke_claude(prompt, SELF_IMPROVE_ALLOWED_TOOLS, cwd=cwd)

    if working_tree_clean(cwd):
        return {"applied": False, "reason": "no changes made", "claude_result": claude_result}

    test_result = run_tests(cwd)
    if test_result.returncode != 0:
        git("reset", "--hard", "HEAD", cwd=cwd)
        git("clean", "-fd", cwd=cwd)
        return {
            "applied": False,
            "reason": "tests failed after self-edit, changes reverted",
            "test_output": (test_result.stdout + test_result.stderr)[-4000:],
            "claude_result": claude_result,
        }

    git("add", "-A", cwd=cwd)
    commit_msg = f"self-improve: {instruction[:72]}"
    git("commit", "-m", commit_msg, cwd=cwd)
    commit_hash = git("rev-parse", "HEAD", cwd=cwd).stdout.strip()
    return {
        "applied": True,
        "commit": commit_hash,
        "test_output": test_result.stdout[-2000:],
        "claude_result": claude_result,
    }
