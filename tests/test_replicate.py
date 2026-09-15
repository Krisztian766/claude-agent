import json
import sys
from pathlib import Path

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


def test_first_replica_succeeds(tmp_path, monkeypatch):
    base, replicas_dir, registry_path = setup(tmp_path, monkeypatch)

    result = replicate.spawn_replica(name="r1")

    assert result["spawned"] is True
    assert (replicas_dir / "r1" / "agent.py").exists()
    assert (replicas_dir / "r1" / ".replica_depth").read_text() == "1"
    registry = json.loads(registry_path.read_text())
    assert len(registry["replicas"]) == 1
    assert registry["replicas"][0]["status"] == "alive"


def test_runtime_state_not_copied(tmp_path, monkeypatch):
    base, replicas_dir, _ = setup(tmp_path, monkeypatch)
    (base / "wallet.json").write_text("{}")
    (base / "payment_jobs.json").write_text("[]")

    result = replicate.spawn_replica(name="r1")

    assert result["spawned"] is True
    assert not (replicas_dir / "r1" / "wallet.json").exists()
    assert not (replicas_dir / "r1" / "payment_jobs.json").exists()


def test_duplicate_name_refused(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    replicate.spawn_replica(name="dup")

    result = replicate.spawn_replica(name="dup")

    assert result["spawned"] is False
    assert "already exists" in result["reason"]


def test_cap_enforced(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, max_replicas=2)

    r1 = replicate.spawn_replica(name="r1")
    r2 = replicate.spawn_replica(name="r2")
    r3 = replicate.spawn_replica(name="r3")

    assert r1["spawned"] and r2["spawned"]
    assert r3["spawned"] is False
    assert "cap" in r3["reason"]


def test_depth_limit_enforced(tmp_path, monkeypatch):
    base, _, _ = setup(tmp_path, monkeypatch, max_depth=1)
    (base / ".replica_depth").write_text("1")  # this instance is already at depth 1

    result = replicate.spawn_replica(name="child")

    assert result["spawned"] is False
    assert "depth" in result["reason"]


def test_this_depth_defaults_to_zero(tmp_path, monkeypatch):
    base, _, _ = setup(tmp_path, monkeypatch)
    assert replicate.this_depth() == 0
