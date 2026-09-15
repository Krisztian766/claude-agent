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


def test_build_claude_cmd_omits_model_flag_by_default():
    cmd = claude_client.build_claude_cmd("hi", "Read")
    assert "--model" not in cmd


def test_resolve_model_maps_tiers():
    assert claude_client.resolve_model("cheap") == "haiku"
    assert claude_client.resolve_model("expensive") == "sonnet"
    assert claude_client.resolve_model(None) is None
    assert claude_client.resolve_model("claude-opus-5") == "claude-opus-5"  # passthrough for raw aliases


def test_build_claude_cmd_includes_resolved_model():
    cmd = claude_client.build_claude_cmd("hi", "Read", model="cheap")
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "haiku"


def test_invoke_claude_passes_model_through():
    fake = MagicMock(returncode=0, stdout=json.dumps({"result": "ok"}), stderr="")
    with patch("claude_client.subprocess.run", return_value=fake) as run_mock:
        claude_client.invoke_claude("hi", "Read", model="expensive")

    called_cmd = run_mock.call_args[0][0]
    assert "--model" in called_cmd
    assert called_cmd[called_cmd.index("--model") + 1] == "sonnet"
