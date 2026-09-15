import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import outreach  # noqa: E402


def setup(tmp_path, monkeypatch):
    drafts_dir = tmp_path / "outreach" / "drafts"
    jobs_path = tmp_path / "payment_jobs.json"
    monkeypatch.setattr(outreach, "DRAFTS_DIR", drafts_dir)
    monkeypatch.setattr(outreach, "JOBS_PATH", jobs_path)
    return drafts_dir, jobs_path


def test_stats_summary_counts_done_jobs(tmp_path, monkeypatch):
    _, jobs_path = setup(tmp_path, monkeypatch)
    jobs_path.write_text(json.dumps({
        "a": {"status": "done"}, "b": {"status": "done"}, "c": {"status": "processing"},
    }))
    stats = outreach.stats_summary()
    assert stats == {"jobs_done": 2, "jobs_total": 3}


def test_stats_summary_empty_when_no_jobs_file(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    assert outreach.stats_summary() == {"jobs_done": 0, "jobs_total": 0}


def test_draft_outreach_writes_file(tmp_path, monkeypatch):
    drafts_dir, _ = setup(tmp_path, monkeypatch)
    with patch("outreach.invoke_claude", return_value={"result": "Try my agent!"}):
        result = outreach.draft_outreach()

    assert result["drafted"] is True
    assert Path(result["path"]).read_text().strip() == "Try my agent!"
    assert list(drafts_dir.glob("*.txt"))


def test_draft_outreach_never_uses_write_edit_bash_tools(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    captured = {}

    def fake_invoke(prompt, tools):
        captured["tools"] = tools
        return {"result": "text"}

    with patch("outreach.invoke_claude", side_effect=fake_invoke):
        outreach.draft_outreach()

    for forbidden in ("Write", "Edit", "Bash"):
        assert forbidden not in captured["tools"]


def test_draft_outreach_handles_error():
    with patch("outreach.invoke_claude", return_value={"error": "boom"}):
        result = outreach.draft_outreach()
    assert result["drafted"] is False


def test_draft_outreach_handles_empty_response(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    with patch("outreach.invoke_claude", return_value={"result": "   "}):
        result = outreach.draft_outreach()
    assert result["drafted"] is False
