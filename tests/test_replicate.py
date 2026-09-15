import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import replicate  # noqa: E402


def setup(tmp_path, monkeypatch, max_replicas=3, max_depth=2):
    base = tmp_path / "instance"
    base.mkdir()
    (base / "agent.py").write_text("# fake agent\n")
    replicas_dir = tmp_path / "replicas"
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(replicate, "BASE_DIR", base)
    monkeypatch.setattr(replicate, "REPLICAS_DIR", replicas_dir)
    monkeypatch.setattr(replicate, "REGISTRY_PATH", registry_path)
    monkeypatch.setattr(replicate, "MAX_REPLICAS", max_replicas)
    monkeypatch.setattr(replicate, "MAX_DEPTH", max_depth)
    return base, replicas_dir, registry_path


def spawn_without_starting_process(*args, **kwargs):
    """Tests use a fake instance dir with no real venv/autonomous.py, so
    actually exec-ing a subprocess would fail -- every test that calls
    spawn_replica() patches _start_process out. Returns the CURRENT test
    process's own pid (always genuinely alive) rather than an arbitrary
    fake one, so spawn_replica()'s automatic reap_dead_replicas() call
    doesn't reap it out from under unrelated tests -- see
    test_reap_dead_replicas_* for tests that specifically exercise reaping."""
    with patch("replicate._start_process", return_value=os.getpid()):
        return replicate.spawn_replica(*args, **kwargs)


def test_first_replica_succeeds(tmp_path, monkeypatch):
    base, replicas_dir, registry_path = setup(tmp_path, monkeypatch)

    result = spawn_without_starting_process(name="r1")

    assert result["spawned"] is True
    assert (replicas_dir / "r1" / "agent.py").exists()
    assert (replicas_dir / "r1" / ".replica_depth").read_text() == "1"
    registry = json.loads(registry_path.read_text())
    assert len(registry["replicas"]) == 1
    assert registry["replicas"][0]["status"] == "alive"
    assert registry["replicas"][0]["wallet_address"] == result["wallet_address"]
    assert registry["replicas"][0]["pid"] == os.getpid()


def test_replica_gets_its_own_fresh_wallet_not_the_parents(tmp_path, monkeypatch):
    base, replicas_dir, _ = setup(tmp_path, monkeypatch)
    base_wallet = {"address": "0xPARENT", "private_key": "0xparentkey"}
    (base / "wallet.json").write_text(json.dumps(base_wallet))
    (base / "payment_jobs.json").write_text("[]")

    result = spawn_without_starting_process(name="r1")

    assert result["spawned"] is True
    replica_wallet = json.loads((replicas_dir / "r1" / "wallet.json").read_text())
    assert replica_wallet["address"] != base_wallet["address"]
    assert replica_wallet["address"] == result["wallet_address"]
    assert not (replicas_dir / "r1" / "payment_jobs.json").exists()


def test_spawn_actually_starts_a_process(tmp_path, monkeypatch):
    base, replicas_dir, _ = setup(tmp_path, monkeypatch)

    with patch("replicate._start_process", return_value=999) as start:
        result = replicate.spawn_replica(name="r1")

    start.assert_called_once()
    assert result["pid"] == 999


def test_duplicate_name_refused(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    spawn_without_starting_process(name="dup")

    result = spawn_without_starting_process(name="dup")

    assert result["spawned"] is False
    assert "already exists" in result["reason"]


def test_cap_enforced(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, max_replicas=2)

    r1 = spawn_without_starting_process(name="r1")
    r2 = spawn_without_starting_process(name="r2")
    r3 = spawn_without_starting_process(name="r3")

    assert r1["spawned"] and r2["spawned"]
    assert r3["spawned"] is False
    assert "cap" in r3["reason"]


def test_is_process_alive_true_for_current_process():
    assert replicate.is_process_alive(os.getpid()) is True


def test_is_process_alive_false_for_nonexistent_pid():
    # A pid that (almost certainly) doesn't exist.
    assert replicate.is_process_alive(999999) is False


def test_is_process_alive_false_for_no_pid():
    assert replicate.is_process_alive(None) is False
    assert replicate.is_process_alive(0) is False


def test_reap_dead_replicas_marks_dead_and_frees_cap_slot(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, max_replicas=1)

    spawn_without_starting_process(name="r1")
    registry_path = replicate.REGISTRY_PATH
    registry = json.loads(registry_path.read_text())
    registry["replicas"][0]["pid"] = 999999  # simulate a crashed process
    registry_path.write_text(json.dumps(registry))

    # Cap is 1 and the (fake) alive replica occupies it -- but it's actually
    # dead, so spawn_replica()'s automatic reap should free the slot.
    result = spawn_without_starting_process(name="r2")

    assert result["spawned"] is True
    registry = json.loads(registry_path.read_text())
    statuses = {r["name"]: r["status"] for r in registry["replicas"]}
    assert statuses["r1"] == "dead"
    assert statuses["r2"] == "alive"


def test_reap_dead_replicas_leaves_alive_ones_alone(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    # spawn_without_starting_process registers the CURRENT test process's own
    # pid (see its docstring) -- genuinely alive, so it must not be reaped.
    spawn_without_starting_process(name="r1")

    result = replicate.reap_dead_replicas()

    assert result["reaped"] == []
    registry = json.loads(replicate.REGISTRY_PATH.read_text())
    assert registry["replicas"][0]["status"] == "alive"


def test_depth_limit_enforced(tmp_path, monkeypatch):
    base, _, _ = setup(tmp_path, monkeypatch, max_depth=1)
    (base / ".replica_depth").write_text("1")  # this instance is already at depth 1

    result = spawn_without_starting_process(name="child")

    assert result["spawned"] is False
    assert "depth" in result["reason"]


def test_this_depth_defaults_to_zero(tmp_path, monkeypatch):
    base, _, _ = setup(tmp_path, monkeypatch)
    assert replicate.this_depth() == 0
