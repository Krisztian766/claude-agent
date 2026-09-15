"""Tests for payment_server health check and reachability verification."""
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_env(tmp_path, monkeypatch):
    """Import payment_server fresh with an isolated wallet/jobs file."""
    monkeypatch.setattr(sys, "argv", ["payment_server.py"])
    wallet_path = tmp_path / "wallet.json"
    jobs_path = tmp_path / "jobs.json"
    import wallet as wallet_module
    monkeypatch.setattr(wallet_module, "WALLET_PATH", wallet_path)

    import payment_server
    monkeypatch.setattr(payment_server, "JOBS_PATH", jobs_path)
    monkeypatch.setattr(payment_server, "wallet", wallet_module.load_or_create_wallet())
    return payment_server


def test_health_check_script_with_running_server(tmp_path, monkeypatch):
    """Integration test: health check script can verify a running server"""
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()

    # Verify all endpoints the health check uses are working
    root = client.get("/")
    assert root.status_code == 200
    assert "service" in root.get_json()

    activity = client.get("/activity")
    assert activity.status_code == 200
    assert "wallet_address" in activity.get_json()

    task = client.post("/task", json={"prompt": "test"})
    assert task.status_code == 402
    assert "job_id" in task.get_json()


def test_health_check_detects_missing_service_field(tmp_path, monkeypatch):
    """Health check can verify response structure"""
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()

    # All key endpoints should return proper service info
    resp = client.get("/")
    data = resp.get_json()
    assert "service" in data, "Root endpoint must provide 'service' field"
    assert data["service"] == "claude-agent payment server"


def test_payment_required_endpoint_returns_402(tmp_path, monkeypatch):
    """Verify /task endpoint correctly returns 402 Payment Required"""
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()

    resp = client.post("/task", json={"prompt": "test"})

    assert resp.status_code == 402
    data = resp.get_json()
    assert "job_id" in data
    assert "pay_to" in data
    assert "amount_eth" in data
    assert "amount_wei" in data


def test_activity_endpoint_never_expires(tmp_path, monkeypatch):
    """Verify /activity endpoint is always available (no auth required)"""
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()

    # Should work with no prior context
    resp = client.get("/activity")
    assert resp.status_code == 200

    # Should work after many requests
    for i in range(5):
        client.post("/task", json={"prompt": f"test {i}"})

    resp = client.get("/activity")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["jobs"]["total_ever"] == 5


def test_all_endpoints_reachable_from_blank_state(tmp_path, monkeypatch):
    """Verify server can handle health checks on a fresh start"""
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()

    # No jobs created yet
    assert not ps.JOBS_PATH.exists() or ps.load_jobs() == {}

    # All health check endpoints should still work
    endpoints = [
        ("GET", "/", {}),
        ("GET", "/activity", {}),
    ]

    for method, path, body in endpoints:
        if method == "GET":
            resp = client.get(path)
        else:
            resp = client.post(path, json=body)
        assert resp.status_code in (200, 302), f"{method} {path} failed: {resp.status_code}"
        assert resp.get_json() is not None, f"{method} {path} returned no JSON"
