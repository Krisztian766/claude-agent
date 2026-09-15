#!/usr/bin/env python3
"""Generic task-running agent built on the Claude Code CLI.

Uses your Claude subscription (whatever `claude` is logged in as on this
machine) as the reasoning engine, invoked non-interactively via `claude -p`.
No Anthropic API key, no per-token billing -- this rides the same plan
Claude Code itself uses interactively.

Usage:
    agent.py submit "prompt text" [--name foo] [--tools "Read Write Bash"]
    agent.py run                        # process whatever's pending, then exit
    agent.py watch                      # keep polling inbox/ forever
    agent.py status                     # show pending / recently completed tasks
    agent.py self-improve "instruction" # let the agent edit its own code (git+test gated)
    agent.py replicate [--name id]      # spawn a bounded copy of this agent

Per-task tool access:
    By default a task can only use read-only/research tools (see
    DEFAULT_ALLOWED_TOOLS below) -- it cannot run shell commands or write
    files unless you opt in via `submit --tools` or a hand-written sidecar
    file "<task>.task.tools" next to a manually dropped *.task file.

    Tasks that originate from the payment server (payment_server.py, i.e.
    from anonymous strangers who paid) are ALWAYS forced onto the safe
    default tools regardless of any --tools override -- see payment_server.py.
"""
import argparse
import json
import logging
import sys
import time
import uuid
from pathlib import Path

from claude_client import invoke_claude

BASE_DIR = Path(__file__).resolve().parent
INBOX_DIR = BASE_DIR / "inbox"
DONE_DIR = BASE_DIR / "done"
LOG_FILE = BASE_DIR / "agent.log"

DEFAULT_ALLOWED_TOOLS = "Read Grep Glob WebFetch WebSearch"
POLL_INTERVAL_SEC = 10

log = logging.getLogger("claude-agent")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
    )


def tools_sidecar_for(task_path: Path) -> Path:
    return task_path.with_suffix(task_path.suffix + ".tools")


def run_task(task_path: Path) -> None:
    prompt = task_path.read_text().strip()
    if not prompt:
        log.warning("Empty task, skipping: %s", task_path.name)
        archive(task_path, {"error": "empty task file"})
        return

    tools_file = tools_sidecar_for(task_path)
    allowed_tools = tools_file.read_text().strip() if tools_file.exists() else DEFAULT_ALLOWED_TOOLS

    log.info("Running task %s (tools: %s)", task_path.name, allowed_tools)
    payload = invoke_claude(prompt, allowed_tools)
    if "error" in payload:
        log.error("Task %s failed: %s", task_path.name, str(payload["error"])[:500])
    else:
        log.info("Task %s done", task_path.name)
    archive(task_path, payload)


def archive(task_path: Path, payload: dict) -> None:
    DONE_DIR.mkdir(exist_ok=True)
    out_path = DONE_DIR / (task_path.stem + ".result.json")
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tools_file = tools_sidecar_for(task_path)
    if tools_file.exists():
        tools_file.rename(DONE_DIR / tools_file.name)
    task_path.rename(DONE_DIR / task_path.name)


def process_pending() -> None:
    INBOX_DIR.mkdir(exist_ok=True)
    for task_path in sorted(INBOX_DIR.glob("*.task")):
        run_task(task_path)


def cmd_submit(args: argparse.Namespace) -> None:
    INBOX_DIR.mkdir(exist_ok=True)
    name = args.name or f"task-{uuid.uuid4().hex[:8]}"
    task_path = INBOX_DIR / f"{name}.task"
    if task_path.exists():
        print(f"Már létezik: {task_path} -- válassz másik --name-et.", file=sys.stderr)
        sys.exit(1)
    task_path.write_text(args.prompt.strip() + "\n")
    if args.tools:
        tools_sidecar_for(task_path).write_text(args.tools.strip() + "\n")
    print(f"Beadva: {task_path.name}")
    if args.run:
        run_task(task_path)


def cmd_run(_args: argparse.Namespace) -> None:
    log.info("Claude agent: single pass")
    process_pending()


def cmd_watch(_args: argparse.Namespace) -> None:
    log.info("Claude agent: watch mode (poll every %ds)", POLL_INTERVAL_SEC)
    while True:
        process_pending()
        time.sleep(POLL_INTERVAL_SEC)


def cmd_status(_args: argparse.Namespace) -> None:
    INBOX_DIR.mkdir(exist_ok=True)
    DONE_DIR.mkdir(exist_ok=True)
    pending = sorted(INBOX_DIR.glob("*.task"))
    done = sorted(DONE_DIR.glob("*.result.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    print(f"Várakozó ({len(pending)}):")
    for p in pending:
        print(f"  - {p.name}")
    print(f"\nUtolsó 10 kész ({len(done)} összesen):")
    for p in done[:10]:
        try:
            payload = json.loads(p.read_text())
        except json.JSONDecodeError:
            payload = {}
        status = "HIBA" if "error" in payload else "OK"
        summary = str(payload.get("error") or payload.get("result") or "")[:80]
        print(f"  - [{status}] {p.name}: {summary}")


def cmd_self_improve(args: argparse.Namespace) -> None:
    import self_improve
    result = self_improve.self_improve(args.instruction)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("applied"):
        sys.exit(1)


def cmd_replicate(args: argparse.Namespace) -> None:
    import replicate
    result = replicate.spawn_replica(name=args.name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("spawned"):
        sys.exit(1)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Claude Code CLI-re épülő agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_submit = sub.add_parser("submit", help="Új feladat beadása")
    p_submit.add_argument("prompt", help="A feladat szövege (Claude promptja)")
    p_submit.add_argument("--name", help="Task azonosító (fájlnév alap), alapból random")
    p_submit.add_argument("--tools", help="Engedélyezett eszközök, pl. \"Read Write Bash\"")
    p_submit.add_argument("--run", action="store_true", help="Rögtön futtasd is le beadás után")
    p_submit.set_defaults(func=cmd_submit)

    p_run = sub.add_parser("run", help="Várakozó feladatok egyszeri feldolgozása")
    p_run.set_defaults(func=cmd_run)

    p_watch = sub.add_parser("watch", help="Folyamatos figyelés, örökre fut")
    p_watch.set_defaults(func=cmd_watch)

    p_status = sub.add_parser("status", help="Várakozó és kész feladatok listája")
    p_status.set_defaults(func=cmd_status)

    p_self_improve = sub.add_parser("self-improve", help="Az agent szerkeszti a saját kódját (git+teszt védett)")
    p_self_improve.add_argument("instruction", help="Mit javítson/fejlesszen magán")
    p_self_improve.set_defaults(func=cmd_self_improve)

    p_replicate = sub.add_parser("replicate", help="Új, korlátozott számú példány indítása magáról")
    p_replicate.add_argument("--name", help="Az új példány azonosítója")
    p_replicate.set_defaults(func=cmd_replicate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
