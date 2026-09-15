"""Shared wrapper around the `claude` CLI. Used by agent.py, self_improve.py,
replicate.py and payment_server.py so there's one place that knows how to
invoke Claude Code non-interactively."""
import json
import subprocess

CLAUDE_TIMEOUT_SEC = 900


def build_claude_cmd(prompt: str, allowed_tools: str) -> list:
    return [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--permission-prompts", "none",
        "--allowedTools", allowed_tools,
    ]


def invoke_claude(prompt: str, allowed_tools: str, cwd=None, timeout: int = CLAUDE_TIMEOUT_SEC) -> dict:
    """Runs one `claude -p` call and returns a result payload dict.
    Never raises -- failures come back as {"error": ...} so callers can
    always archive/report something."""
    cmd = build_claude_cmd(prompt, allowed_tools)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return {"error": f"timeout after {timeout}s"}
    except FileNotFoundError:
        return {"error": "claude CLI not found on PATH"}

    if result.returncode != 0:
        return {"error": result.stderr[:2000] or f"exit {result.returncode}"}

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw_output": result.stdout}
