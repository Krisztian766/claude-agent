import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import autonomous  # noqa: E402


def write_jobs(path, jobs):
    path.write_text(json.dumps(jobs))


def test_processing_backlog_counts_only_processing(tmp_path, monkeypatch):
    jobs_path = tmp_path / "jobs.json"
    monkeypatch.setattr(autonomous, "JOBS_PATH", jobs_path)
    write_jobs(jobs_path, {
        "a": {"status": "processing"},
        "b": {"status": "processing"},
        "c": {"status": "done"},
        "d": {"status": "awaiting_payment"},
    })
    assert autonomous.processing_backlog() == 2


def test_processing_backlog_zero_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(autonomous, "JOBS_PATH", tmp_path / "missing.json")
    assert autonomous.processing_backlog() == 0


def test_maybe_replicate_skips_below_threshold(tmp_path, monkeypatch):
    jobs_path = tmp_path / "jobs.json"
    monkeypatch.setattr(autonomous, "JOBS_PATH", jobs_path)
    monkeypatch.setattr(autonomous, "REPLICATE_BACKLOG_THRESHOLD", 3)
    write_jobs(jobs_path, {"a": {"status": "processing"}})

    with patch("autonomous.replicate_module.spawn_replica") as spawn:
        result = autonomous.maybe_replicate()

    spawn.assert_not_called()
    assert result["replicated"] is False


def test_maybe_replicate_triggers_at_threshold(tmp_path, monkeypatch):
    jobs_path = tmp_path / "jobs.json"
    monkeypatch.setattr(autonomous, "JOBS_PATH", jobs_path)
    monkeypatch.setattr(autonomous, "REPLICATE_BACKLOG_THRESHOLD", 2)
    write_jobs(jobs_path, {"a": {"status": "processing"}, "b": {"status": "processing"}})

    with patch("autonomous.replicate_module.spawn_replica", return_value={"spawned": True, "name": "x"}) as spawn:
        result = autonomous.maybe_replicate()

    spawn.assert_called_once()
    assert result["spawned"] is True


def test_decide_self_improvement_returns_empty_on_none():
    with patch("autonomous.invoke_claude", return_value={"result": "NONE"}):
        assert autonomous.decide_self_improvement() == ""


def test_decide_self_improvement_returns_empty_on_error():
    with patch("autonomous.invoke_claude", return_value={"error": "boom"}):
        assert autonomous.decide_self_improvement() == ""


def test_decide_self_improvement_returns_instruction():
    with patch("autonomous.invoke_claude", return_value={"result": "Fix the flaky retry logic"}):
        assert autonomous.decide_self_improvement() == "Fix the flaky retry logic"


def test_decide_self_improvement_never_reads_payment_jobs():
    # Security-critical: payment_jobs.json holds raw text submitted by
    # anonymous strangers. Now that self_improve.py has Bash access, feeding
    # that text into this decision step would be an indirect prompt-
    # injection path. See autonomous.py's decide_self_improvement docstring.
    captured = {}

    def fake_invoke(prompt, tools):
        captured["prompt"] = prompt
        return {"result": "NONE"}

    with patch("autonomous.invoke_claude", side_effect=fake_invoke):
        autonomous.decide_self_improvement()

    assert "payment_jobs.json" not in captured["prompt"]


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


def test_tick_calls_both_checks():
    with patch("autonomous.maybe_replicate", return_value={"replicated": False}) as mr, \
         patch("autonomous.maybe_self_improve", return_value={"applied": False}) as mi:
        result = autonomous.tick()

    mr.assert_called_once()
    mi.assert_called_once()
    assert "replicate" in result and "self_improve" in result
