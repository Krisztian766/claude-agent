import json
import subprocess as sp
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claude_client  # noqa: E402


def test_invoke_claude_success():
    fake = MagicMock(returncode=0, stdout=json.dumps({"result": "ok"}), stderr="")
    with patch("claude_client.subprocess.run", return_value=fake):
        payload = claude_client.invoke_claude("hi", "Read")
    assert payload == {"result": "ok"}


def test_invoke_claude_nonzero_exit():
    fake = MagicMock(returncode=1, stdout="", stderr="boom")
    with patch("claude_client.subprocess.run", return_value=fake):
        payload = claude_client.invoke_claude("hi", "Read")
    assert payload == {"error": "boom"}


def test_invoke_claude_bad_json_falls_back_to_raw_output():
    fake = MagicMock(returncode=0, stdout="not json", stderr="")
    with patch("claude_client.subprocess.run", return_value=fake):
        payload = claude_client.invoke_claude("hi", "Read")
    assert payload == {"raw_output": "not json"}


def test_invoke_claude_timeout():
    with patch("claude_client.subprocess.run", side_effect=sp.TimeoutExpired(cmd="claude", timeout=1)):
        payload = claude_client.invoke_claude("hi", "Read")
    assert "timeout" in payload["error"]


def test_invoke_claude_missing_binary():
    with patch("claude_client.subprocess.run", side_effect=FileNotFoundError()):
        payload = claude_client.invoke_claude("hi", "Read")
    assert "not found" in payload["error"]


def test_build_claude_cmd_includes_deny_by_default_prompts():
    cmd = claude_client.build_claude_cmd("hi", "Read Write")
    assert "--permission-prompts" in cmd
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert "--allowedTools" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "Read Write"
