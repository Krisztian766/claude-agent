import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import autonomous  # noqa: E402


def test_maybe_reproduce_skips_below_threshold():
    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.vitality.can_reproduce", return_value=False), \
         patch("autonomous.replicate_module.spawn_replica") as spawn:
        result = autonomous.maybe_reproduce()

    spawn.assert_not_called()
    assert result["reproduced"] is False


def test_maybe_reproduce_spawns_and_funds_when_healthy():
    with patch("autonomous.vitality.balance_wei", return_value=10**17), \
         patch("autonomous.vitality.can_reproduce", return_value=True), \
         patch("autonomous.replicate_module.spawn_replica",
               return_value={"spawned": True, "name": "x", "wallet_address": "0xABC"}) as spawn, \
         patch("autonomous.vitality.fund_offspring", return_value={"funded": True}) as fund:
        result = autonomous.maybe_reproduce()

    spawn.assert_called_once()
    fund.assert_called_once_with("0xABC")
    assert result["spawned"] is True
    assert result["inheritance"] == {"funded": True}


def test_maybe_reproduce_does_not_fund_when_spawn_refused():
    with patch("autonomous.vitality.balance_wei", return_value=10**17), \
         patch("autonomous.vitality.can_reproduce", return_value=True), \
         patch("autonomous.replicate_module.spawn_replica",
               return_value={"spawned": False, "reason": "replica cap (3) reached"}), \
         patch("autonomous.vitality.fund_offspring") as fund:
        result = autonomous.maybe_reproduce()

    fund.assert_not_called()
    assert result["spawned"] is False


def test_decide_self_improvement_returns_empty_on_none():
    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", return_value={"result": "FEELING: fine\nDECISION: NONE"}):
        assert autonomous.decide_self_improvement() == ""


def test_decide_self_improvement_returns_empty_on_error():
    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", return_value={"error": "boom"}):
        assert autonomous.decide_self_improvement() == ""


def test_decide_self_improvement_returns_instruction():
    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", return_value={"result": "FEELING: okay\nDECISION: Fix the flaky retry logic"}):
        assert autonomous.decide_self_improvement() == "Fix the flaky retry logic"


def test_decide_self_improvement_passes_feeling_to_status_report():
    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report") as wsr, \
         patch("autonomous.invoke_claude", return_value={"result": "FEELING: doing great, earned two jobs today\nDECISION: NONE"}):
        autonomous.decide_self_improvement()

    wsr.assert_called_once_with("doing great, earned two jobs today")


def test_decide_self_improvement_mentions_learnings_file_and_balance():
    captured = {}

    def fake_invoke(prompt, tools):
        captured["prompt"] = prompt
        return {"result": "FEELING: fine\nDECISION: NONE"}

    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", side_effect=fake_invoke):
        autonomous.decide_self_improvement()

    assert "LEARNINGS.md" in captured["prompt"]
    assert "goal is to stay alive and grow" in captured["prompt"]


def test_decide_self_improvement_never_reads_payment_jobs():
    # Security-critical: payment_jobs.json holds raw text submitted by
    # anonymous strangers. Now that self_improve.py has Bash access, feeding
    # that text into this decision step would be an indirect prompt-
    # injection path. See autonomous.py's decide_self_improvement docstring.
    captured = {}

    def fake_invoke(prompt, tools):
        captured["prompt"] = prompt
        return {"result": "FEELING: fine\nDECISION: NONE"}

    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", side_effect=fake_invoke):
        autonomous.decide_self_improvement()

    assert "payment_jobs.json" not in captured["prompt"]


def test_parse_feeling_and_decision_standard_format():
    text = "FEELING: I'm doing okay, balance is stable\nDECISION: NONE"
    feeling, decision = autonomous._parse_feeling_and_decision(text)
    assert feeling == "I'm doing okay, balance is stable"
    assert decision == "NONE"


def test_parse_feeling_and_decision_with_real_instruction():
    text = "FEELING: a bit low on funds\nDECISION: Fix the retry bug in payment_server.py"
    feeling, decision = autonomous._parse_feeling_and_decision(text)
    assert feeling == "a bit low on funds"
    assert decision == "Fix the retry bug in payment_server.py"


def test_parse_feeling_and_decision_falls_back_when_unformatted():
    # Model didn't follow the format -- old-style plain text should still
    # work as a decision, just with no feeling captured.
    feeling, decision = autonomous._parse_feeling_and_decision("Just fix the thing")
    assert feeling == ""
    assert decision == "Just fix the thing"


def test_self_improve_commit_count_counts_only_self_improve_commits(tmp_path, monkeypatch):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "f.txt").write_text("1")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    (repo / "f.txt").write_text("2")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "self-improve: fixed a thing"], cwd=repo, check=True)

    monkeypatch.setattr(autonomous, "BASE_DIR", repo)
    assert autonomous.self_improve_commit_count() == 1


def test_write_status_report_creates_readable_file(tmp_path, monkeypatch):
    status_file = tmp_path / "STATUS.md"
    monkeypatch.setattr(autonomous, "STATUS_FILE", status_file)
    monkeypatch.setattr(autonomous, "BASE_DIR", tmp_path)

    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.vitality.is_alive", return_value=True), \
         patch("autonomous.replicate_module.load_registry", return_value={"replicas": []}), \
         patch("autonomous.replicate_module.alive_count", return_value=0), \
         patch("autonomous.self_improve_commit_count", return_value=4), \
         patch("autonomous.git", return_value=type("R", (), {"stdout": "M STATUS.md\n", "returncode": 0})()) as git_mock:
        autonomous.write_status_report("feeling pretty good today")

    content = status_file.read_text()
    assert "feeling pretty good today" in content
    assert "Önjavítások eddig:** 4" in content
    assert "igen" in content  # alive

    calls = [c.args for c in git_mock.call_args_list]
    assert ("add", "STATUS.md") in calls
    assert ("commit", "-m", "status: automatic update") in calls
    assert ("push", "origin", "master") in calls


def test_write_status_report_skips_commit_when_nothing_changed(tmp_path, monkeypatch):
    status_file = tmp_path / "STATUS.md"
    monkeypatch.setattr(autonomous, "STATUS_FILE", status_file)
    monkeypatch.setattr(autonomous, "BASE_DIR", tmp_path)

    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.vitality.is_alive", return_value=True), \
         patch("autonomous.replicate_module.load_registry", return_value={"replicas": []}), \
         patch("autonomous.replicate_module.alive_count", return_value=0), \
         patch("autonomous.self_improve_commit_count", return_value=4), \
         patch("autonomous.git", return_value=type("R", (), {"stdout": "", "returncode": 0})()) as git_mock:
        autonomous.write_status_report("same as before")

    calls = [c.args for c in git_mock.call_args_list]
    assert ("add", "STATUS.md") in calls
    assert not any(c[0] == "commit" for c in calls)
    assert not any(c[0] == "push" for c in calls)


def test_maybe_self_improve_skips_when_nothing_decided():
    with patch("autonomous.decide_self_improvement", return_value=""), \
         patch("autonomous.self_improve.self_improve") as si:
        result = autonomous.maybe_self_improve()

    si.assert_not_called()
    assert result["applied"] is False


def test_maybe_self_improve_calls_self_improve_when_decided():
    with patch("autonomous.decide_self_improvement", return_value="do the thing"), \
         patch("autonomous.self_improve.self_improve", return_value={"applied": True, "commit": "abc"}) as si:
        result = autonomous.maybe_self_improve()

    si.assert_called_once_with("do the thing")
    assert result["applied"] is True


def test_tick_skips_all_work_when_not_alive():
    with patch("autonomous.vitality.is_alive", return_value=False), \
         patch("autonomous.vitality.balance_wei", return_value=100), \
         patch("autonomous.vitality.pay_upkeep") as upkeep, \
         patch("autonomous.maybe_reproduce") as mr, \
         patch("autonomous.maybe_self_improve") as mi, \
         patch("autonomous.maybe_draft_outreach") as mo:
        result = autonomous.tick()

    upkeep.assert_not_called()
    mr.assert_not_called()
    mi.assert_not_called()
    mo.assert_not_called()
    assert result["alive"] is False
    assert result["balance_wei"] == 100


def test_tick_does_all_work_when_alive():
    with patch("autonomous.vitality.is_alive", return_value=True), \
         patch("autonomous.vitality.pay_upkeep", return_value={"paid": True}) as upkeep, \
         patch("autonomous.maybe_reproduce", return_value={"reproduced": False}) as mr, \
         patch("autonomous.maybe_self_improve", return_value={"applied": False}) as mi, \
         patch("autonomous.maybe_draft_outreach", return_value={"drafted": False}) as mo:
        result = autonomous.tick()

    upkeep.assert_called_once()
    mr.assert_called_once()
    mi.assert_called_once()
    mo.assert_called_once()
    assert result["alive"] is True
    assert "reproduce" in result and "self_improve" in result and "outreach" in result


def test_run_forever_exits_after_applied_self_improve_for_systemd_restart():
    # When self-improve actually lands a code change, this process must exit
    # (not keep looping with the OLD code still in memory) so systemd's
    # Restart=always brings it back up running the new code.
    with patch("autonomous.setup_logging"), \
         patch("autonomous.tick", return_value={
             "alive": True,
             "reproduce": {"reproduced": False},
             "self_improve": {"applied": True, "commit": "abc123"},
             "outreach": {"drafted": False},
         }) as tick_mock, \
         patch("autonomous.time.sleep") as sleep_mock:
        autonomous.run_forever()

    tick_mock.assert_called_once()
    sleep_mock.assert_not_called()


def test_run_forever_keeps_looping_when_nothing_applied():
    calls = {"n": 0}

    def fake_tick():
        calls["n"] += 1
        if calls["n"] >= 3:
            raise SystemExit  # break out of the infinite loop for the test
        return {"alive": True, "reproduce": {"reproduced": False}, "self_improve": {"applied": False}, "outreach": {"drafted": False}}

    with patch("autonomous.setup_logging"), \
         patch("autonomous.tick", side_effect=fake_tick), \
         patch("autonomous.time.sleep") as sleep_mock:
        try:
            autonomous.run_forever()
        except SystemExit:
            pass

    assert calls["n"] == 3
    assert sleep_mock.call_count == 2


def test_run_forever_handles_dead_tick_result_without_crashing():
    # tick() can return self_improve=None when not alive -- run_forever must
    # not crash trying to call .get("applied") on None.
    calls = {"n": 0}

    def fake_tick():
        calls["n"] += 1
        if calls["n"] >= 2:
            raise SystemExit
        return {"alive": False, "balance_wei": 1, "reproduce": None, "self_improve": None, "outreach": None}

    with patch("autonomous.setup_logging"), \
         patch("autonomous.tick", side_effect=fake_tick), \
         patch("autonomous.time.sleep"):
        try:
            autonomous.run_forever()
        except SystemExit:
            pass

    assert calls["n"] == 2
