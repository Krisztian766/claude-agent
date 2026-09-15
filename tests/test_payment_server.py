import json
import multiprocessing
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _acquire_jobs_lock_and_record(hold_time, events, idx):
    """Run in a separate OS process (see
    test_jobs_file_lock_excludes_across_separate_processes below): holds
    _jobs_file_lock() for hold_time seconds and records enter/exit
    timestamps, so the test can check two real processes -- not just two
    threads in one process -- never hold the lock at the same time."""
    import payment_server as ps
    with ps._jobs_file_lock():
        events.append((idx, "enter", time.monotonic()))
        time.sleep(hold_time)
        events.append((idx, "exit", time.monotonic()))


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


def test_concurrent_confirm_with_same_tx_hash_pays_only_one_job(tmp_path, monkeypatch):
    """Regression test for the TOCTOU race: two /confirm calls for
    different jobs racing on the same tx_hash must not both be accepted,
    even when the on-chain verification is slow. Before the fix, the
    "already used" check and the tx_hash write were separate steps with
    the slow web3 calls in between, so both requests could pass the check
    before either finished verifying."""
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()
    submit1 = client.post("/task", json={"prompt": "one"}).get_json()
    submit2 = client.post("/task", json={"prompt": "two"}).get_json()

    fake_tx = {"to": ps.wallet["address"], "value": ps.PRICE_WEI}
    fake_receipt = MagicMock(status=1)

    barrier = threading.Barrier(2)
    call_count = {"n": 0}
    count_lock = threading.Lock()

    def slow_get_transaction(tx_hash):
        with count_lock:
            call_count["n"] += 1
        try:
            barrier.wait(timeout=0.2)
        except threading.BrokenBarrierError:
            pass
        time.sleep(0.05)
        return fake_tx

    results = {}

    def call(job_id, key):
        results[key] = client.post(f"/task/{job_id}/confirm", json={"tx_hash": "0xrace"})

    with patch.object(ps.web3.eth, "get_transaction", side_effect=slow_get_transaction), \
         patch.object(ps.web3.eth, "get_transaction_receipt", return_value=fake_receipt), \
         patch("payment_server.invoke_claude", return_value={"result": "ok"}):
        t1 = threading.Thread(target=call, args=(submit1["job_id"], "a"))
        t2 = threading.Thread(target=call, args=(submit2["job_id"], "b"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    codes = sorted(r.status_code for r in results.values())
    assert codes == [202, 402]

    # The reservation happens before verification, so the loser must never
    # even reach the (slow) on-chain check.
    assert call_count["n"] == 1

    jobs = ps.load_jobs()
    claimed = [j for j in jobs.values() if j.get("tx_hash") == "0xrace"]
    assert len(claimed) == 1
    active = [j for j in jobs.values() if j["status"] in ("processing", "done")]
    assert len(active) == 1


def test_confirm_rolls_back_reservation_when_verification_fails(tmp_path, monkeypatch):
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()
    submit = client.post("/task", json={"prompt": "hi"}).get_json()

    bad_tx = {"to": "0x000000000000000000000000000000000000dead", "value": ps.PRICE_WEI}
    fake_receipt = MagicMock(status=1)
    with patch.object(ps.web3.eth, "get_transaction", return_value=bad_tx), \
         patch.object(ps.web3.eth, "get_transaction_receipt", return_value=fake_receipt):
        resp = client.post(f"/task/{submit['job_id']}/confirm", json={"tx_hash": "0xbad"})

    assert resp.status_code == 402
    job = ps.load_jobs()[submit["job_id"]]
    assert job["status"] == "awaiting_payment"
    assert job["tx_hash"] is None

    # A rolled-back reservation must not block a later, valid confirm.
    good_tx = {"to": ps.wallet["address"], "value": ps.PRICE_WEI}
    with patch.object(ps.web3.eth, "get_transaction", return_value=good_tx), \
         patch.object(ps.web3.eth, "get_transaction_receipt", return_value=fake_receipt), \
         patch("payment_server.invoke_claude", return_value={"result": "ok"}):
        resp2 = client.post(f"/task/{submit['job_id']}/confirm", json={"tx_hash": "0xgood"})

    assert resp2.status_code == 202


def test_activity_endpoint_never_exposes_raw_prompt(tmp_path, monkeypatch):
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()
    secret_prompt = "SECRET_PAYLOAD_SHOULD_NOT_LEAK"
    client.post("/task", json={"prompt": secret_prompt})

    resp = client.get("/activity")
    body = resp.get_json()

    assert resp.status_code == 200
    assert secret_prompt not in json.dumps(body)
    assert body["jobs"]["awaiting_payment"] == 1
    assert body["jobs"]["total_ever"] == 1
    assert "wallet_address" in body
    assert resp.headers["Access-Control-Allow-Origin"] == ps.ACTIVITY_CORS_ORIGIN


def test_activity_reports_replica_and_self_improve_stats(tmp_path, monkeypatch):
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()

    with patch("payment_server.replicate_module.load_registry", return_value={"replicas": [
        {"status": "alive"}, {"status": "alive"},
    ]}), patch("payment_server.replicate_module.MAX_REPLICAS", 3), \
         patch("payment_server.self_improve_stats", return_value={"total_commits": 5, "last_commit_message": "self-improve: did a thing"}):
        resp = client.get("/activity")

    body = resp.get_json()
    assert body["replicas"] == {"alive": 2, "max": 3}
    assert body["self_improve"]["total_commits"] == 5


def test_jobs_file_lock_excludes_across_separate_processes(tmp_path, monkeypatch):
    """Regression test for gunicorn's --workers 2 deployment: the jobs lock
    must be a real cross-process lock (flock on a file), not a
    threading.Lock, which would only ever synchronize threads inside a
    single worker and do nothing across the two independent worker
    processes gunicorn actually runs."""
    ps = make_env(tmp_path, monkeypatch)
    assert ps.JOBS_PATH.parent == tmp_path

    manager = multiprocessing.Manager()
    events = manager.list()

    p1 = multiprocessing.Process(target=_acquire_jobs_lock_and_record, args=(0.3, events, 1))
    p2 = multiprocessing.Process(target=_acquire_jobs_lock_and_record, args=(0.3, events, 2))
    p1.start()
    p2.start()
    p1.join(timeout=5)
    p2.join(timeout=5)

    assert not p1.is_alive() and not p2.is_alive()
    assert p1.exitcode == 0 and p2.exitcode == 0

    by_idx = {idx: {} for idx in (1, 2)}
    for idx, kind, t in events:
        by_idx[idx][kind] = t
    assert set(by_idx[1]) == {"enter", "exit"}
    assert set(by_idx[2]) == {"enter", "exit"}

    # The two critical sections must not overlap: whichever process got
    # the lock second must not enter before the first one exits.
    overlap = by_idx[1]["enter"] < by_idx[2]["exit"] and by_idx[2]["enter"] < by_idx[1]["exit"]
    assert not overlap


def test_confirm_rejects_expired_job(tmp_path, monkeypatch):
    ps = make_env(tmp_path, monkeypatch)
    client = ps.app.test_client()
    submit = client.post("/task", json={"prompt": "hi"}).get_json()

    jobs = ps.load_jobs()
    jobs[submit["job_id"]]["created_at"] = time.time() - ps.JOB_EXPIRY_SEC - 10
    ps.save_jobs(jobs)

    resp = client.post(f"/task/{submit['job_id']}/confirm", json={"tx_hash": "0xabc"})
    assert resp.status_code == 410
