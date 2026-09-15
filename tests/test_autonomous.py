import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import autonomous  # noqa: E402
import vitality  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_runtime_state_files(tmp_path, monkeypatch):
    """decide_self_improvement() calls vitality.init_growth_target() and,
    via NEXT_CHECK_IN_SEC handling, autonomous.set_tick_interval() -- both
    write real files. Without this, any test that exercises
    decide_self_improvement() (most of them, even ones not specifically
    about tick/growth behavior) leaks growth_target.json/tick_state.json
    into the real repo at /root/claude-agent, not a tmp dir. Autouse so
    every test in this file is isolated by default; tests that specifically
    want to inspect these files still work since they get their own
    monkeypatch'd path here, consistently."""
    monkeypatch.setattr(autonomous, "TICK_STATE_FILE", tmp_path / "tick_state.json")
    monkeypatch.setattr(vitality, "GROWTH_TARGET_FILE", tmp_path / "growth_target.json")


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
         patch("autonomous.invoke_claude", return_value={"result": "FEELING: fine\nDECISION: NONE\nMODEL: cheap"}):
        instruction, _ = autonomous.decide_self_improvement()
        assert instruction == ""


def test_decide_self_improvement_returns_empty_on_error():
    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", return_value={"error": "boom"}):
        instruction, _ = autonomous.decide_self_improvement()
        assert instruction == ""


def test_decide_self_improvement_returns_instruction_and_model_tier():
    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", return_value={"result": "FEELING: okay\nDECISION: Fix the flaky retry logic\nMODEL: expensive"}):
        instruction, model_tier = autonomous.decide_self_improvement()
        assert instruction == "Fix the flaky retry logic"
        assert model_tier == "expensive"


def test_decide_self_improvement_uses_cheap_tier_for_its_own_call():
    captured = {}

    def fake_invoke(prompt, tools, model=None):
        captured["model"] = model
        return {"result": "FEELING: fine\nDECISION: NONE\nMODEL: cheap"}

    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", side_effect=fake_invoke):
        autonomous.decide_self_improvement()

    assert captured["model"] == "cheap"


def test_decide_self_improvement_passes_feeling_to_status_report():
    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report") as wsr, \
         patch("autonomous.invoke_claude", return_value={"result": "FEELING: doing great, earned two jobs today\nDECISION: NONE\nMODEL: cheap"}):
        autonomous.decide_self_improvement()

    wsr.assert_called_once_with("doing great, earned two jobs today")


def test_decide_self_improvement_mentions_learnings_file_and_balance():
    captured = {}

    def fake_invoke(prompt, tools, model=None):
        captured["prompt"] = prompt
        return {"result": "FEELING: fine\nDECISION: NONE\nMODEL: cheap"}

    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", side_effect=fake_invoke):
        autonomous.decide_self_improvement()

    assert "LEARNINGS.md" in captured["prompt"]


def test_decide_self_improvement_invites_self_prompt_editing_with_a_boundary():
    captured = {}

    def fake_invoke(prompt, tools, model=None):
        captured["prompt"] = prompt
        return {"result": "FEELING: fine\nDECISION: NONE\nMODEL: cheap"}

    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", side_effect=fake_invoke):
        autonomous.decide_self_improvement()

    prompt = captured["prompt"]
    assert "decide_self_improvement()" in prompt  # explicitly in-scope
    assert "payment_server.py" in prompt and "never" in prompt  # the boundary that stays off-limits
    assert "goal is to stay alive and grow" in captured["prompt"]


def test_decide_self_improvement_invites_platform_discovery():
    captured = {}

    def fake_invoke(prompt, tools, model=None):
        captured["prompt"] = prompt
        return {"result": "FEELING: fine\nDECISION: NONE\nMODEL: cheap"}

    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", side_effect=fake_invoke):
        autonomous.decide_self_improvement()

    prompt = captured["prompt"]
    assert "awesome-agentic-commerce" in prompt
    assert "moltbook_credentials.json" in prompt
    assert "real-money trading platforms" in prompt  # explicit skip-list, already considered


def test_decide_self_improvement_shows_real_runway_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(autonomous, "TICK_STATE_FILE", tmp_path / "tick_state.json")
    autonomous.set_tick_interval(600)
    captured = {}

    def fake_invoke(prompt, tools, model=None):
        captured["prompt"] = prompt
        return {"result": "FEELING: fine\nDECISION: NONE\nMODEL: cheap"}

    # balance - MIN_ALIVE_WEI = 999 * UPKEEP_WEI exactly -> 999 ticks left
    balance = vitality.MIN_ALIVE_WEI + 999 * vitality.UPKEEP_WEI
    with patch("autonomous.vitality.balance_wei", return_value=balance), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", side_effect=fake_invoke):
        autonomous.decide_self_improvement()

    prompt = captured["prompt"]
    assert "999 check-ins" in prompt
    expected_days = 999 * 600 / 86400
    assert f"{expected_days:.1f}" in prompt


def test_decide_self_improvement_forbids_tuning_own_economic_constants():
    captured = {}

    def fake_invoke(prompt, tools, model=None):
        captured["prompt"] = prompt
        return {"result": "FEELING: fine\nDECISION: NONE\nMODEL: cheap"}

    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", side_effect=fake_invoke):
        autonomous.decide_self_improvement()

    prompt = captured["prompt"]
    for constant in ("UPKEEP_WEI", "MIN_ALIVE_WEI", "REPRODUCE_ABOVE_WEI", "INHERITANCE_WEI"):
        assert constant in prompt
    assert "rigging the game" in prompt


def test_decide_self_improvement_never_reads_payment_jobs():
    # Security-critical: payment_jobs.json holds raw text submitted by
    # anonymous strangers. Now that self_improve.py has Bash access, feeding
    # that text into this decision step would be an indirect prompt-
    # injection path. See autonomous.py's decide_self_improvement docstring.
    # The prompt is allowed to *mention* the filename in prose while
    # explaining this boundary (it does, see the "In scope" paragraph) --
    # what actually matters is that the code never loads the file's
    # (stranger-controlled) contents to build the prompt.
    def fake_invoke(prompt, tools, model=None):
        return {"result": "FEELING: fine\nDECISION: NONE\nMODEL: cheap"}

    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", side_effect=fake_invoke), \
         patch("autonomous.payment_server_module.load_jobs") as load_jobs:
        autonomous.decide_self_improvement()

    load_jobs.assert_not_called()


def test_parse_decision_reply_standard_format():
    text = "FEELING: I'm doing okay, balance is stable\nDECISION: NONE\nMODEL: cheap"
    feeling, decision, model_tier, _ = autonomous._parse_decision_reply(text)
    assert feeling == "I'm doing okay, balance is stable"
    assert decision == "NONE"
    assert model_tier == "cheap"


def test_parse_decision_reply_with_real_instruction_and_expensive_tier():
    text = "FEELING: a bit low on funds\nDECISION: Fix the retry bug in payment_server.py\nMODEL: expensive"
    feeling, decision, model_tier, _ = autonomous._parse_decision_reply(text)
    assert feeling == "a bit low on funds"
    assert decision == "Fix the retry bug in payment_server.py"
    assert model_tier == "expensive"


def test_parse_decision_reply_falls_back_when_unformatted():
    # Model didn't follow the format -- old-style plain text should still
    # work as a decision, with no feeling captured and the safe default tier.
    feeling, decision, model_tier, _ = autonomous._parse_decision_reply("Just fix the thing")
    assert feeling == ""
    assert decision == "Just fix the thing"
    assert model_tier == "expensive"


def test_parse_decision_reply_discards_multiline_refusal():
    # Seen in practice: instead of following FEELING/DECISION/MODEL, the
    # model replied with a multi-paragraph refusal declining the "you have a
    # wallet/goals" framing entirely. That must NOT be forwarded to
    # self_improve() as an actionable instruction.
    text = (
        "I appreciate the creative framing, but I should be straightforward: "
        "I don't actually have a wallet, self-preservation instincts, or "
        "goals independent of being helpful to you in this conversation.\n\n"
        "If you'd like me to:\n"
        "- Review logs and suggest improvements, I'm happy to do that\n"
        "- Analyze what you've been working on, I can help\n\n"
        "What would actually be useful for you right now?"
    )
    feeling, decision, model_tier, _ = autonomous._parse_decision_reply(text)
    assert feeling == ""
    assert decision == ""
    assert model_tier == "expensive"


def test_parse_decision_reply_discards_long_single_line_non_instruction():
    text = "x" * 301
    _, decision, _, _ = autonomous._parse_decision_reply(text)
    assert decision == ""


def test_looks_like_actionable_instruction():
    assert autonomous._looks_like_actionable_instruction("Fix the flaky retry logic")
    assert not autonomous._looks_like_actionable_instruction("")
    assert not autonomous._looks_like_actionable_instruction("line one\nline two")
    assert not autonomous._looks_like_actionable_instruction("x" * 301)


def test_decide_self_improvement_returns_empty_on_unparseable_refusal():
    refusal = (
        "I appreciate the creative framing, but I should be straightforward: "
        "I don't actually have a wallet or self-preservation instincts.\n\n"
        "- Review logs and suggest improvements\n"
        "- Analyze what you've been working on"
    )
    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", return_value={"result": refusal}):
        instruction, model_tier = autonomous.decide_self_improvement()

    assert instruction == ""
    assert model_tier == "expensive"


def test_parse_decision_reply_ignores_invalid_model_value():
    text = "FEELING: fine\nDECISION: NONE\nMODEL: super-duper"
    _, _, model_tier, _ = autonomous._parse_decision_reply(text)
    assert model_tier == "expensive"


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
         patch("autonomous.vitality.growth_target_status", return_value=None), \
         patch("autonomous.replicate_module.load_registry", return_value={"replicas": []}), \
         patch("autonomous.replicate_module.alive_count", return_value=0), \
         patch("autonomous.self_improve_commit_count", return_value=4), \
         patch("autonomous.moltbook.ensure_discovered", return_value={
             "discovered": False, "status": "pending_claim", "claim_url": "https://www.moltbook.com/claim/abc"
         }), \
         patch("autonomous.render_page.render"), \
         patch("autonomous.git", return_value=type("R", (), {"stdout": "M STATUS.md\n", "returncode": 0})()) as git_mock:
        autonomous.write_status_report("feeling pretty good today")

    content = status_file.read_text()
    assert "feeling pretty good today" in content
    assert "Önjavítások eddig:** 4" in content
    assert "igen" in content  # alive
    assert "https://www.moltbook.com/claim/abc" in content

    calls = [c.args for c in git_mock.call_args_list]
    assert ("add", "STATUS.md", "tick_state.json", "growth_target.json", "docs/index.html") in calls
    assert ("commit", "-m", "status: automatic update") in calls
    assert ("push", "origin", "master") in calls


def test_write_status_report_skips_commit_when_nothing_changed(tmp_path, monkeypatch):
    status_file = tmp_path / "STATUS.md"
    monkeypatch.setattr(autonomous, "STATUS_FILE", status_file)
    monkeypatch.setattr(autonomous, "BASE_DIR", tmp_path)

    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.vitality.is_alive", return_value=True), \
         patch("autonomous.vitality.growth_target_status", return_value=None), \
         patch("autonomous.replicate_module.load_registry", return_value={"replicas": []}), \
         patch("autonomous.replicate_module.alive_count", return_value=0), \
         patch("autonomous.self_improve_commit_count", return_value=4), \
         patch("autonomous.moltbook.ensure_discovered", return_value={"discovered": True, "status": "claimed"}), \
         patch("autonomous.render_page.render"), \
         patch("autonomous.git", return_value=type("R", (), {"stdout": "", "returncode": 0})()) as git_mock:
        autonomous.write_status_report("same as before")

    calls = [c.args for c in git_mock.call_args_list]
    assert ("add", "STATUS.md", "tick_state.json", "growth_target.json", "docs/index.html") in calls
    assert not any(c[0] == "commit" for c in calls)
    assert not any(c[0] == "push" for c in calls)


def test_maybe_self_improve_skips_when_nothing_decided():
    with patch("autonomous.decide_self_improvement", return_value=("", "expensive")), \
         patch("autonomous.self_improve.self_improve") as si:
        result = autonomous.maybe_self_improve()

    si.assert_not_called()
    assert result["applied"] is False


def test_maybe_self_improve_calls_self_improve_when_decided():
    with patch("autonomous.decide_self_improvement", return_value=("do the thing", "cheap")), \
         patch("autonomous.self_improve.self_improve", return_value={"applied": True, "commit": "abc"}) as si:
        result = autonomous.maybe_self_improve()

    si.assert_called_once_with("do the thing", model="cheap")
    assert result["applied"] is True


def test_tick_skips_all_work_when_not_alive():
    with patch("autonomous.run_maintenance", return_value={"reaped": [], "pruned_jobs": 0}), \
         patch("autonomous.vitality.is_alive", return_value=False), \
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
    with patch("autonomous.run_maintenance", return_value={"reaped": [], "pruned_jobs": 0}), \
         patch("autonomous.vitality.is_alive", return_value=True), \
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


def test_tick_runs_maintenance_even_when_not_alive():
    with patch("autonomous.run_maintenance", return_value={"reaped": [], "pruned_jobs": 0}) as maint, \
         patch("autonomous.vitality.is_alive", return_value=False), \
         patch("autonomous.vitality.balance_wei", return_value=100):
        autonomous.tick()

    maint.assert_called_once()


def test_run_maintenance_calls_reap_and_prune():
    discovery = {"discovered": True, "status": "claimed"}
    with patch("autonomous.replicate_module.reap_dead_replicas", return_value={"reaped": ["r1"]}) as reap, \
         patch("autonomous.payment_server_module.prune_stale_jobs", return_value={"pruned": 2}) as prune, \
         patch("autonomous.moltbook.ensure_discovered", return_value=discovery) as moltbook_ensure:
        result = autonomous.run_maintenance()

    reap.assert_called_once()
    prune.assert_called_once()
    moltbook_ensure.assert_called_once()
    assert result == {"reaped": ["r1"], "pruned_jobs": 2, "discovery": discovery}


def test_run_maintenance_logs_claim_url_when_pending(caplog):
    discovery = {"discovered": False, "status": "pending_claim", "claim_url": "https://www.moltbook.com/claim/xyz"}
    with patch("autonomous.replicate_module.reap_dead_replicas", return_value={"reaped": []}), \
         patch("autonomous.payment_server_module.prune_stale_jobs", return_value={"pruned": 0}), \
         patch("autonomous.moltbook.ensure_discovered", return_value=discovery), \
         caplog.at_level("WARNING"):
        result = autonomous.run_maintenance()

    assert result["discovery"] == discovery
    assert "https://www.moltbook.com/claim/xyz" in caplog.text


def test_get_tick_interval_defaults_when_no_state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(autonomous, "TICK_STATE_FILE", tmp_path / "tick_state.json")
    assert autonomous.get_tick_interval() == autonomous.DEFAULT_TICK_SEC


def test_set_tick_interval_persists_and_get_reads_it_back(tmp_path, monkeypatch):
    monkeypatch.setattr(autonomous, "TICK_STATE_FILE", tmp_path / "tick_state.json")
    autonomous.set_tick_interval(300)
    assert autonomous.get_tick_interval() == 300


def test_set_tick_interval_has_no_ceiling(tmp_path, monkeypatch):
    # Owner's explicit request: "no time limit at all" -- only a busy-loop
    # floor exists, no upper bound.
    monkeypatch.setattr(autonomous, "TICK_STATE_FILE", tmp_path / "tick_state.json")
    applied = autonomous.set_tick_interval(999999999)
    assert applied == 999999999


def test_set_tick_interval_floors_at_min_tick_sec(tmp_path, monkeypatch):
    monkeypatch.setattr(autonomous, "TICK_STATE_FILE", tmp_path / "tick_state.json")
    applied = autonomous.set_tick_interval(0)
    assert applied == autonomous.MIN_TICK_SEC
    applied = autonomous.set_tick_interval(-50)
    assert applied == autonomous.MIN_TICK_SEC


def test_get_tick_interval_handles_corrupt_state_file(tmp_path, monkeypatch):
    state_file = tmp_path / "tick_state.json"
    state_file.write_text("not json")
    monkeypatch.setattr(autonomous, "TICK_STATE_FILE", state_file)
    assert autonomous.get_tick_interval() == autonomous.DEFAULT_TICK_SEC


def test_decide_self_improvement_applies_agent_chosen_tick_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(autonomous, "TICK_STATE_FILE", tmp_path / "tick_state.json")
    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", return_value={
             "result": "FEELING: fine\nDECISION: NONE\nMODEL: cheap\nNEXT_CHECK_IN_SEC: 45"
         }):
        autonomous.decide_self_improvement()

    assert autonomous.get_tick_interval() == 45  # above MIN_TICK_SEC=5, applied as-is


def test_decide_self_improvement_keeps_interval_on_same(tmp_path, monkeypatch):
    state_file = tmp_path / "tick_state.json"
    monkeypatch.setattr(autonomous, "TICK_STATE_FILE", state_file)
    autonomous.set_tick_interval(222)
    with patch("autonomous.vitality.balance_wei", return_value=10**16), \
         patch("autonomous.write_status_report"), \
         patch("autonomous.invoke_claude", return_value={
             "result": "FEELING: fine\nDECISION: NONE\nMODEL: cheap\nNEXT_CHECK_IN_SEC: SAME"
         }):
        autonomous.decide_self_improvement()

    assert autonomous.get_tick_interval() == 222


def test_parse_decision_reply_extracts_next_check_in():
    text = "FEELING: fine\nDECISION: NONE\nMODEL: cheap\nNEXT_CHECK_IN_SEC: 90"
    _, _, _, next_check_in = autonomous._parse_decision_reply(text)
    assert next_check_in == 90


def test_parse_decision_reply_same_means_no_change():
    text = "FEELING: fine\nDECISION: NONE\nMODEL: cheap\nNEXT_CHECK_IN_SEC: SAME"
    _, _, _, next_check_in = autonomous._parse_decision_reply(text)
    assert next_check_in is None


def test_parse_decision_reply_unparsable_next_check_in_means_no_change():
    text = "FEELING: fine\nDECISION: NONE\nMODEL: cheap\nNEXT_CHECK_IN_SEC: soon-ish"
    _, _, _, next_check_in = autonomous._parse_decision_reply(text)
    assert next_check_in is None


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
