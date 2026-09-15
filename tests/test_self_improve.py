import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import self_improve  # noqa: E402


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n*.pyc\n")
    (repo / "src.py").write_text("VALUE = 1\n")
    (repo / "tests" / "test_src.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
        "import src\n"
        "def test_value():\n"
        "    assert src.VALUE == 1\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_self_improve_now_has_bash_access():
    # Intentional, owner-approved 2026-09-15 (see self_improve.py module
    # docstring for the security tradeoff this represents).
    assert "Bash" in self_improve.SELF_IMPROVE_ALLOWED_TOOLS


def test_refuses_when_tree_dirty(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "src.py").write_text("VALUE = 2\n")  # uncommitted change

    result = self_improve.self_improve("do something", cwd=repo)

    assert result["applied"] is False
    assert "not clean" in result["reason"]


def test_no_changes_made(tmp_path):
    repo = make_repo(tmp_path)

    with patch("self_improve.invoke_claude", return_value={"result": "nothing to do"}):
        result = self_improve.self_improve("do nothing", cwd=repo)

    assert result["applied"] is False
    assert result["reason"] == "no changes made"


def test_successful_self_edit_commits(tmp_path):
    repo = make_repo(tmp_path)

    def fake_invoke(prompt, tools, cwd=None, model=None):
        (repo / "src.py").write_text("VALUE = 1\nEXTRA = 42\n")
        (repo / "LEARNINGS.md").write_text("# Learnings\n\n- added EXTRA\n")
        return {"result": "added EXTRA"}

    with patch("self_improve.invoke_claude", side_effect=fake_invoke):
        result = self_improve.self_improve("add EXTRA constant", cwd=repo)

    assert result["applied"] is True
    assert result["commit"]
    log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=repo, capture_output=True, text=True).stdout
    assert "self-improve" in log


def test_model_tier_passed_through_to_invoke_claude(tmp_path):
    repo = make_repo(tmp_path)
    captured = {}

    def fake_invoke(prompt, tools, cwd=None, model=None):
        captured["model"] = model
        (repo / "src.py").write_text("VALUE = 1\nEXTRA = 1\n")
        (repo / "LEARNINGS.md").write_text("# Learnings\n\n- x\n")
        return {"result": "ok"}

    with patch("self_improve.invoke_claude", side_effect=fake_invoke):
        self_improve.self_improve("do something", cwd=repo, model="cheap")

    assert captured["model"] == "cheap"


def test_push_failure_does_not_revert_an_already_good_commit(tmp_path):
    # The test repo has no "origin" remote, so the push is expected to fail
    # here -- applied must still be True (a tested, committed change is real
    # and safe locally regardless of push outcome), just reported honestly.
    repo = make_repo(tmp_path)

    def fake_invoke(prompt, tools, cwd=None, model=None):
        (repo / "src.py").write_text("VALUE = 1\nEXTRA = 42\n")
        (repo / "LEARNINGS.md").write_text("# Learnings\n\n- added EXTRA\n")
        return {"result": "added EXTRA"}

    with patch("self_improve.invoke_claude", side_effect=fake_invoke):
        result = self_improve.self_improve("add EXTRA constant", cwd=repo)

    assert result["applied"] is True
    assert result["pushed"] is False
    assert result["push_error"]


def test_prompt_instructs_updating_learnings_file(tmp_path):
    repo = make_repo(tmp_path)
    captured = {}

    def fake_invoke(prompt, tools, cwd=None, model=None):
        captured["prompt"] = prompt
        (repo / "src.py").write_text("VALUE = 1\nEXTRA = 1\n")
        return {"result": "ok"}

    with patch("self_improve.invoke_claude", side_effect=fake_invoke):
        self_improve.self_improve("do something", cwd=repo)

    assert "LEARNINGS.md" in captured["prompt"]


def test_commit_missing_learnings_update_gets_reverted(tmp_path):
    repo = make_repo(tmp_path)
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    def fake_invoke(prompt, tools, cwd=None, model=None):
        # Makes a valid, test-passing change but never touches LEARNINGS.md.
        (repo / "src.py").write_text("VALUE = 1\nEXTRA = 42\n")
        return {"result": "added EXTRA, forgot LEARNINGS.md"}

    with patch("self_improve.invoke_claude", side_effect=fake_invoke):
        result = self_improve.self_improve("add EXTRA constant", cwd=repo)

    assert result["applied"] is False
    assert "LEARNINGS.md" in result["reason"]
    assert self_improve.working_tree_clean(cwd=repo)
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    assert head_after == head_before
    assert (repo / "src.py").read_text() == "VALUE = 1\n"


def test_commit_with_learnings_update_is_applied(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "LEARNINGS.md").write_text("# Learnings\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add learnings file"], cwd=repo, check=True)

    def fake_invoke(prompt, tools, cwd=None, model=None):
        (repo / "src.py").write_text("VALUE = 1\nEXTRA = 42\n")
        (repo / "LEARNINGS.md").write_text("# Learnings\n\n- did a thing\n")
        return {"result": "added EXTRA and logged it"}

    with patch("self_improve.invoke_claude", side_effect=fake_invoke):
        result = self_improve.self_improve("add EXTRA constant", cwd=repo)

    assert result["applied"] is True
    assert result["commit"]
    assert self_improve.commit_touched_learnings(result["commit"], cwd=repo)


def test_failing_tests_get_reverted(tmp_path):
    repo = make_repo(tmp_path)

    def fake_invoke(prompt, tools, cwd=None, model=None):
        (repo / "src.py").write_text("VALUE = 999\n")  # breaks test_value
        return {"result": "broke it"}

    with patch("self_improve.invoke_claude", side_effect=fake_invoke):
        result = self_improve.self_improve("break things", cwd=repo)

    assert result["applied"] is False
    assert "reverted" in result["reason"]
    assert self_improve.working_tree_clean(cwd=repo)
    assert (repo / "src.py").read_text() == "VALUE = 1\n"


def test_failing_tests_revert_also_cleans_untracked_files(tmp_path):
    repo = make_repo(tmp_path)

    def fake_invoke(prompt, tools, cwd=None, model=None):
        (repo / "src.py").write_text("VALUE = 999\n")  # breaks test_value
        (repo / "new_untracked.py").write_text("junk = 1\n")  # never added to git
        return {"result": "broke it and left a stray file"}

    with patch("self_improve.invoke_claude", side_effect=fake_invoke):
        result = self_improve.self_improve("break things and leave a mess", cwd=repo)

    assert result["applied"] is False
    assert "reverted" in result["reason"]
    assert self_improve.working_tree_clean(cwd=repo)
    assert not (repo / "new_untracked.py").exists()

    # A clean tree must allow a subsequent self-improve run to proceed.
    with patch("self_improve.invoke_claude", return_value={"result": "nothing to do"}):
        next_result = self_improve.self_improve("do nothing", cwd=repo)
    assert next_result["reason"] != "working tree not clean, refusing to self-improve"
