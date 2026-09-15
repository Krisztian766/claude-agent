"""Self-replication, bounded hard.

Conway-Research/automaton-style agents spin up copies of themselves when
"profitable." Since 2026-09-15 (see vitality.py) there IS a real profit
signal -- the wallet's own Sepolia balance -- so autonomous.py's tick can
decide to reproduce on its own when vitality.can_reproduce() is true. This
module still enforces the hard caps regardless of who calls it.

A spawned replica is a REAL, RUNNING process (its own `autonomous.py`,
detached, not a systemd unit -- so it doesn't survive a reboot, keeping
sprawl bounded), with its OWN freshly generated Sepolia wallet, typically
funded by the parent via vitality.fund_offspring() right after spawning.

Registry lives OUTSIDE any single instance's own directory
(REGISTRY_PATH, a fixed absolute path) so the cap is global no matter which
copy calls this.
"""
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from eth_account import Account

BASE_DIR = Path(__file__).resolve().parent
REPLICAS_DIR = Path("/root/claude-agent-replicas")
REGISTRY_PATH = Path("/root/claude-agent-registry.json")

MAX_REPLICAS = 3       # total simultaneously-registered replicas, hard cap
MAX_DEPTH = 2           # a replica of a replica of a replica is refused

# Copied into each replica; venv/ included so it works standalone without
# re-installing deps. Runtime/instance-specific state is never copied.
EXCLUDE = {".git", "inbox", "done", "agent.log", "__pycache__", ".pytest_cache",
           "claude-agent-replicas", "wallet.json", "payment_jobs.json"}


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"replicas": []}
    try:
        return json.loads(REGISTRY_PATH.read_text())
    except json.JSONDecodeError:
        return {"replicas": []}


def save_registry(registry: dict) -> None:
    REGISTRY_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2))


def alive_count(registry: dict) -> int:
    return sum(1 for r in registry["replicas"] if r.get("status") == "alive")


def is_process_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, just not signalable -- still alive
    else:
        return True


def reap_dead_replicas() -> dict:
    """Marks registry entries "dead" if their process actually isn't
    running anymore (crashed, OOM-killed, box rebooted -- replicas are
    plain detached processes, not systemd units, so nothing else notices
    this). Without this, alive_count() stays wrong forever and the cap can
    get stuck refusing real future reproduction for replicas that no
    longer exist."""
    registry = load_registry()
    reaped = []
    for r in registry["replicas"]:
        if r.get("status") == "alive" and not is_process_alive(r.get("pid")):
            r["status"] = "dead"
            reaped.append(r["name"])
    if reaped:
        save_registry(registry)
    return {"reaped": reaped}


def this_depth() -> int:
    """0 for the original /root/claude-agent, N for a replica N levels deep
    (read from a marker file a replica gets at spawn time)."""
    marker = BASE_DIR / ".replica_depth"
    if marker.exists():
        try:
            return int(marker.read_text().strip())
        except ValueError:
            return 0
    return 0


def _create_wallet(dest: Path) -> str:
    """Generates a fresh Sepolia keypair for a new replica and writes it in
    the exact format wallet.py's load_or_create_wallet() expects, so the
    replica's own process just finds it already there on first use instead
    of generating a second, different wallet."""
    acct = Account.create()
    wallet = {"address": acct.address, "private_key": acct.key.hex()}
    wallet_path = dest / "wallet.json"
    wallet_path.write_text(json.dumps(wallet, indent=2))
    wallet_path.chmod(0o600)
    return acct.address


def _start_process(dest: Path) -> int:
    """Launches the replica's own autonomous.py as a real, detached
    background process (not a systemd unit -- deliberately doesn't survive
    a reboot, so replica sprawl can't outlive the box being restarted).
    Returns autonomous.py's pid (the one tracked for reap_dead_replicas)."""
    python = dest / "venv" / "bin" / "python3"
    log_path = dest / "autonomous.log"
    with open(log_path, "a") as logf:
        proc = subprocess.Popen(
            [str(python), str(dest / "autonomous.py")],
            cwd=dest,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    # Also start the plain task-queue watcher (agent.py watch), so a
    # replica can actually be delegated work -- see delegate_task() below.
    # Without this, a replica's inbox/ just sits there unprocessed; only
    # autonomous.py (self-improve/reproduce/outreach) was being started
    # before this, which never looks at inbox/ at all.
    agent_log_path = dest / "agent.log"
    with open(agent_log_path, "a") as logf:
        subprocess.Popen(
            [str(python), str(dest / "agent.py"), "watch"],
            cwd=dest,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    return proc.pid


def delegate_task(replica_name: str, prompt: str, tools: str = None) -> dict:
    """Owner/agent can hand a specific replica ("child"/"employee") a task
    to work on, via the exact same inbox/*.task mechanism agent.py submit
    uses locally -- just written directly into that replica's own inbox/
    (its agent.py watch process, started by _start_process(), picks it up).
    Same default-safe-tools-unless-specified rule as any other task."""
    registry = load_registry()
    match = next((r for r in registry["replicas"] if r["name"] == replica_name and r.get("status") == "alive"), None)
    if match is None:
        return {"delegated": False, "reason": f"no alive replica named '{replica_name}'"}

    replica_path = Path(match["path"])
    inbox = replica_path / "inbox"
    if not inbox.exists():
        return {"delegated": False, "reason": f"replica path missing inbox/: {replica_path}"}

    task_name = f"delegated-{uuid.uuid4().hex[:8]}"
    task_path = inbox / f"{task_name}.task"
    task_path.write_text(prompt.strip() + "\n")
    if tools:
        (inbox / f"{task_name}.task.tools").write_text(tools.strip() + "\n")

    return {"delegated": True, "replica": replica_name, "task_name": task_name}


def spawn_replica(name: str = None) -> dict:
    reap_dead_replicas()
    registry = load_registry()
    depth = this_depth()

    if depth >= MAX_DEPTH:
        return {"spawned": False, "reason": f"max depth ({MAX_DEPTH}) reached, refusing to replicate further"}

    if alive_count(registry) >= MAX_REPLICAS:
        return {"spawned": False, "reason": f"replica cap ({MAX_REPLICAS}) reached"}

    name = name or f"replica-{uuid.uuid4().hex[:8]}"
    REPLICAS_DIR.mkdir(exist_ok=True)
    dest = REPLICAS_DIR / name
    if dest.exists():
        return {"spawned": False, "reason": f"a replica named '{name}' already exists"}

    shutil.copytree(BASE_DIR, dest, ignore=shutil.ignore_patterns(*EXCLUDE))
    (dest / "inbox").mkdir(exist_ok=True)
    (dest / "done").mkdir(exist_ok=True)
    (dest / ".replica_depth").write_text(str(depth + 1))
    wallet_address = _create_wallet(dest)
    pid = _start_process(dest)

    registry["replicas"].append({
        "name": name,
        "path": str(dest),
        "parent": str(BASE_DIR),
        "depth": depth + 1,
        "wallet_address": wallet_address,
        "pid": pid,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "alive",
    })
    save_registry(registry)

    return {
        "spawned": True, "name": name, "path": str(dest), "depth": depth + 1,
        "wallet_address": wallet_address, "pid": pid,
    }
