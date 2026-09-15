import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent  # noqa: E402


def make_dirs(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    done = tmp_path / "done"
    monkeypatch.setattr(agent, "INBOX_DIR", inbox)
    monkeypatch.setattr(agent, "DONE_DIR", done)
    return inbox, done


def test_tools_sidecar_for():
    p = Path("/x/inbox/foo.task")
    assert agent.tools_sidecar_for(p) == Path("/x/inbox/foo.task.tools")


def test_archive_moves_task_and_writes_result(tmp_path, monkeypatch):
    inbox, done = make_dirs(tmp_path, monkeypatch)
    inbox.mkdir()
    task_path = inbox / "mytask.task"
    task_path.write_text("do something")

    agent.archive(task_path, {"result": "done!"})

    assert not task_path.exists()
    assert (done / "mytask.task").exists()
    result_path = done / "mytask.result.json"
    assert json.loads(result_path.read_text()) == {"result": "done!"}


def test_archive_moves_tools_sidecar_too(tmp_path, monkeypatch):
    inbox, done = make_dirs(tmp_path, monkeypatch)
    inbox.mkdir()
    task_path = inbox / "mytask.task"
    task_path.write_text("do something")
    tools_path = inbox / "mytask.task.tools"
    tools_path.write_text("Read Write")

    agent.archive(task_path, {"result": "done!"})

    assert (done / "mytask.task.tools").exists()
    assert not tools_path.exists()


def test_run_task_empty_file_archives_error(tmp_path, monkeypatch):
    inbox, done = make_dirs(tmp_path, monkeypatch)
    inbox.mkdir()
    task_path = inbox / "empty.task"
    task_path.write_text("   \n")

    agent.run_task(task_path)

    result = json.loads((done / "empty.result.json").read_text())
    assert "error" in result


def test_run_task_uses_default_tools_without_sidecar(tmp_path, monkeypatch):
    inbox, done = make_dirs(tmp_path, monkeypatch)
    inbox.mkdir()
    task_path = inbox / "t.task"
    task_path.write_text("hello")

    captured = {}

    def fake_invoke(prompt, allowed_tools):
        captured["tools"] = allowed_tools
        return {"result": "x"}

    with patch("agent.invoke_claude", side_effect=fake_invoke):
        agent.run_task(task_path)

    assert captured["tools"] == agent.DEFAULT_ALLOWED_TOOLS


def test_run_task_uses_sidecar_tools_when_present(tmp_path, monkeypatch):
    inbox, done = make_dirs(tmp_path, monkeypatch)
    inbox.mkdir()
    task_path = inbox / "t.task"
    task_path.write_text("hello")
    (inbox / "t.task.tools").write_text("Read Write Bash")

    captured = {}

    def fake_invoke(prompt, allowed_tools):
        captured["tools"] = allowed_tools
        return {"result": "x"}

    with patch("agent.invoke_claude", side_effect=fake_invoke):
        agent.run_task(task_path)

    assert captured["tools"] == "Read Write Bash"


def test_process_pending_runs_all_task_files_in_order(tmp_path, monkeypatch):
    inbox, done = make_dirs(tmp_path, monkeypatch)
    inbox.mkdir()
    (inbox / "b.task").write_text("second")
    (inbox / "a.task").write_text("first")

    order = []

    def fake_invoke(prompt, allowed_tools):
        order.append(prompt)
        return {"result": "ok"}

    with patch("agent.invoke_claude", side_effect=fake_invoke):
        agent.process_pending()

    assert order == ["first", "second"]


def test_cmd_submit_writes_task_file(tmp_path, monkeypatch):
    inbox, done = make_dirs(tmp_path, monkeypatch)
    args = argparse.Namespace(prompt="do X", name="mytask", tools=None, run=False)
    agent.cmd_submit(args)
    assert (inbox / "mytask.task").read_text().strip() == "do X"


def test_cmd_submit_refuses_duplicate_name(tmp_path, monkeypatch, capsys):
    inbox, done = make_dirs(tmp_path, monkeypatch)
    inbox.mkdir()
    (inbox / "dup.task").write_text("existing")
    args = argparse.Namespace(prompt="new", name="dup", tools=None, run=False)
    try:
        agent.cmd_submit(args)
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert e.code == 1
