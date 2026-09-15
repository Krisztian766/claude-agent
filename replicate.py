"""Self-replication, bounded hard.

Conway-Research/automaton-style agents spin up copies of themselves when
"profitable." There's no real profit signal here (subscription-based, no
per-call cost), so replication is owner-triggered only (`agent.py replicate`)
-- never something the agent decides for itself -- and capped hard by a
registry file shared across every replica, regardless of which instance
calls spawn_replica().

Registry lives OUTSIDE any single instance's own directory
(REGISTRY_PATH, a fixed absolute path) so the cap is global no matter which
copy calls this.
"""
import json
import shutil
import time
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPLICAS_DIR = Path("/root/claude-agent-replicas")
REGISTRY_PATH = Path("/root/claude-agent-registry.json")

MAX_REPLICAS = 3       # total simultaneously-registered replicas, hard cap
MAX_DEPTH = 2           # a replica of a replica of a replica is refused

# Copied into each replica; venv/ included so it works standalone without
# re-installing deps. Runtime/instance-specific state is never copied.
EXCLUDE = {".git", "inbox", "done", "agent.log", "__pycache__", ".pytest_cache",
           "claude-agent-replicas", "wallet.json"}


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


def spawn_replica(name: str = None) -> dict:
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

    registry["replicas"].append({
        "name": name,
        "path": str(dest),
        "parent": str(BASE_DIR),
        "depth": depth + 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "alive",
    })
    save_registry(registry)

    return {"spawned": True, "name": name, "path": str(dest), "depth": depth + 1}
