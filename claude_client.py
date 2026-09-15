"""Shared wrapper around the `claude` CLI. Used by agent.py, self_improve.py,
replicate.py and payment_server.py so there's one place that knows how to
invoke Claude Code non-interactively."""
import json
import subprocess

CLAUDE_TIMEOUT_SEC = 900

# Tiered model choice (added 2026-09-15, owner's request): callers that let
# the agent pick its own tier pass one of these keys, not a raw model name,
# so the actual model alias can change in one place later.
MODEL_TIERS = {"cheap": "haiku", "expensive": "sonnet"}


def resolve_model(tier: str = None) -> str:
    """tier is "cheap", "expensive", a raw model alias/name, or None (let
    Claude Code use its own configured default)."""
    if not tier:
        return None
    return MODEL_TIERS.get(tier, tier)


def build_claude_cmd(prompt: str, allowed_tools: str, model: str = None) -> list:
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--permission-prompts", "none",
        "--allowedTools", allowed_tools,
    ]
    resolved = resolve_model(model)
    if resolved:
        cmd += ["--model", resolved]
    return cmd


def invoke_claude(prompt: str, allowed_tools: str, cwd=None, timeout: int = CLAUDE_TIMEOUT_SEC, model: str = None) -> dict:
    """Runs one `claude -p` call and returns a result payload dict.
    Never raises -- failures come back as {"error": ...} so callers can
    always archive/report something. `model`: "cheap"/"expensive" tier, a
    raw alias, or None for the default."""
    cmd = build_claude_cmd(prompt, allowed_tools, model=model)
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
