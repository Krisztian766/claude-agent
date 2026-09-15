import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_env(tmp_path, monkeypatch):
    """Import payment_server fresh with an isolated wallet/jobs file, without
    hitting the network (wallet creation is local, Web3 connection object
    construction doesn't itself make a network call)."""
    monkeypatch.setattr(sys, "argv", ["payment_server.py"])
    wallet_path = tmp_path / "wallet.json"
    jobs_path = tmp_path / "jobs.json"
    import wallet as wallet_module
    monkeypatch.setattr(wallet_module, "WALLET_PATH", wallet_path)

    import payment_server
    monkeypatch.setattr(payment_server, "JOBS_PATH", jobs_path)
    monkeypatch.setattr(payment_server, "wallet", wallet_module.load_or_create_wallet())
    return payment_server


def test_submit_task_returns_402_with_payment_info(tmp_path, monkeypatch):
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()

    resp = client.post("/task", json={"prompt": "hello"})

    assert resp.status_code == 402
    body = resp.get_json()
    assert body["pay_to"] == ps.wallet["address"]
    assert body["chain"] == "sepolia"
    assert "job_id" in body


def test_submit_task_missing_prompt(tmp_path, monkeypatch):
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()

    resp = client.post("/task", json={})

    assert resp.status_code == 400


def test_confirm_unknown_job(tmp_path, monkeypatch):
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()

    resp = client.post("/task/doesnotexist/confirm", json={"tx_hash": "0xabc"})

    assert resp.status_code == 404


def test_confirm_rejects_tx_to_wrong_address(tmp_path, monkeypatch):
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()
    submit = client.post("/task", json={"prompt": "hi"}).get_json()

    fake_tx = {"to": "0x000000000000000000000000000000000000dead", "value": ps.PRICE_WEI}
    fake_receipt = MagicMock(status=1)
    with patch.object(ps.web3.eth, "get_transaction", return_value=fake_tx), \
         patch.object(ps.web3.eth, "get_transaction_receipt", return_value=fake_receipt):
        resp = client.post(f"/task/{submit['job_id']}/confirm", json={"tx_hash": "0xdeadbeef"})

    assert resp.status_code == 402
    assert "not verified" in resp.get_json()["error"]


def test_confirm_rejects_insufficient_amount(tmp_path, monkeypatch):
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()
    submit = client.post("/task", json={"prompt": "hi"}).get_json()

    fake_tx = {"to": ps.wallet["address"], "value": ps.PRICE_WEI - 1}
    fake_receipt = MagicMock(status=1)
    with patch.object(ps.web3.eth, "get_transaction", return_value=fake_tx), \
         patch.object(ps.web3.eth, "get_transaction_receipt", return_value=fake_receipt):
        resp = client.post(f"/task/{submit['job_id']}/confirm", json={"tx_hash": "0xdeadbeef"})

    assert resp.status_code == 402


def test_confirm_accepts_valid_payment_and_processes_with_safe_tools_only(tmp_path, monkeypatch):
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()
    submit = client.post("/task", json={"prompt": "hi"}).get_json()

    fake_tx = {"to": ps.wallet["address"], "value": ps.PRICE_WEI}
    fake_receipt = MagicMock(status=1)

    captured = {}

    def fake_invoke(prompt, tools):
        captured["tools"] = tools
        return {"result": "42"}

    with patch.object(ps.web3.eth, "get_transaction", return_value=fake_tx), \
         patch.object(ps.web3.eth, "get_transaction_receipt", return_value=fake_receipt), \
         patch("payment_server.invoke_claude", side_effect=fake_invoke):
        resp = client.post(f"/task/{submit['job_id']}/confirm", json={"tx_hash": "0xdeadbeef"})
        assert resp.status_code == 202
        # process_paid_job runs in a background thread; run it inline here for determinism
        ps.process_paid_job(submit["job_id"])

    assert captured["tools"] == ps.PAYMENT_TASK_ALLOWED_TOOLS
    assert "Bash" not in captured["tools"]
    assert "Write" not in captured["tools"]

    status = client.get(f"/task/{submit['job_id']}").get_json()
    assert status["status"] == "done"
    assert status["result"]["result"] == "42"


def test_confirm_rejects_reused_tx_hash(tmp_path, monkeypatch):
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()
    submit1 = client.post("/task", json={"prompt": "one"}).get_json()
    submit2 = client.post("/task", json={"prompt": "two"}).get_json()

    fake_tx = {"to": ps.wallet["address"], "value": ps.PRICE_WEI}
    fake_receipt = MagicMock(status=1)
    with patch.object(ps.web3.eth, "get_transaction", return_value=fake_tx), \
         patch.object(ps.web3.eth, "get_transaction_receipt", return_value=fake_receipt), \
         patch("payment_server.invoke_claude", return_value={"result": "ok"}):
        r1 = client.post(f"/task/{submit1['job_id']}/confirm", json={"tx_hash": "0xsame"})
        r2 = client.post(f"/task/{submit2['job_id']}/confirm", json={"tx_hash": "0xsame"})

    assert r1.status_code == 202
    assert r2.status_code == 402
    assert "already used" in r2.get_json()["error"]


def test_confirm_rejects_expired_job(tmp_path, monkeypatch):
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()
    submit = client.post("/task", json={"prompt": "hi"}).get_json()

    jobs = ps.load_jobs()
    jobs[submit["job_id"]]["created_at"] = time.time() - ps.JOB_EXPIRY_SEC - 10
    ps.save_jobs(jobs)

    resp = client.post(f"/task/{submit['job_id']}/confirm", json={"tx_hash": "0xabc"})
    assert resp.status_code == 410
