"""x402-style payment-gated task server, Sepolia testnet only.

Flow:
  POST /task {"prompt": "..."}            -> 402 Payment Required + pay_to/amount/job_id
  POST /task/<id>/confirm {"tx_hash": ..} -> verifies the tx on-chain, then runs the task
  GET  /task/<id>                          -> job status / result

SAFETY BOUNDARY (do not relax this without a real conversation about why):
Anything triggered by a stranger's payment runs ONLY with
`agent.DEFAULT_ALLOWED_TOOLS` (read-only-ish: Read/Grep/Glob/WebFetch/
WebSearch). There is no way to request Bash/Write/Edit through this server,
by design -- the request body has no tools field at all. Self-improve and
replicate are not reachable from here either. This server can be paid by
anyone on the internet; it must never be able to touch the filesystem
beyond reading, run shell commands, spawn replicas, or edit code.
"""
import json
import logging
import subprocess
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request
from web3 import Web3

from claude_client import invoke_claude
from wallet import load_or_create_wallet
import replicate as replicate_module

BASE_DIR = Path(__file__).resolve().parent
JOBS_PATH = BASE_DIR / "payment_jobs.json"

SEPOLIA_RPC = "https://ethereum-sepolia-rpc.publicnode.com"
PRICE_ETH = 0.0005
PRICE_WEI = Web3.to_wei(PRICE_ETH, "ether")
JOB_EXPIRY_SEC = 3600
START_TIME = time.time()

# /activity is meant to be polled cross-origin from the public portfolio
# site -- restricted to that one origin, not a wildcard.
ACTIVITY_CORS_ORIGIN = "https://czeczokrisztian.hu"

# Hard-coded, never taken from the request. See module docstring.
PAYMENT_TASK_ALLOWED_TOOLS = "Read Grep Glob WebFetch WebSearch"

log = logging.getLogger("payment-server")
app = Flask(__name__)
web3 = Web3(Web3.HTTPProvider(SEPOLIA_RPC))
wallet = load_or_create_wallet()

_jobs_lock = threading.Lock()


def load_jobs() -> dict:
    if not JOBS_PATH.exists():
        return {}
    try:
        return json.loads(JOBS_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def save_jobs(jobs: dict) -> None:
    JOBS_PATH.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))


def new_job(prompt: str) -> dict:
    job = {
        "id": uuid.uuid4().hex,
        "prompt": prompt,
        "price_wei": PRICE_WEI,
        "status": "awaiting_payment",
        "created_at": time.time(),
        "tx_hash": None,
        "result": None,
    }
    with _jobs_lock:
        jobs = load_jobs()
        jobs[job["id"]] = job
        save_jobs(jobs)
    return job


def get_job(job_id: str) -> dict:
    with _jobs_lock:
        return load_jobs().get(job_id)


def update_job(job_id: str, **fields) -> dict:
    with _jobs_lock:
        jobs = load_jobs()
        if job_id not in jobs:
            return None
        jobs[job_id].update(fields)
        save_jobs(jobs)
        return jobs[job_id]


def verify_payment(job: dict, tx_hash: str) -> tuple:
    """Returns (ok, reason)."""
    with _jobs_lock:
        jobs = load_jobs()
        for other in jobs.values():
            if other["id"] != job["id"] and other.get("tx_hash") == tx_hash:
                return False, "tx_hash already used for another job"

    try:
        tx = web3.eth.get_transaction(tx_hash)
    except Exception as e:
        return False, f"transaction not found: {e}"

    try:
        receipt = web3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        return False, "transaction not yet confirmed"

    if receipt.status != 1:
        return False, "transaction failed on-chain"
    if tx["to"] is None or tx["to"].lower() != wallet["address"].lower():
        return False, "transaction was not sent to this agent's wallet"
    if tx["value"] < job["price_wei"]:
        return False, f"payment too small: got {tx['value']}, need {job['price_wei']}"

    return True, None


def process_paid_job(job_id: str) -> None:
    job = get_job(job_id)
    if job is None:
        return
    log.info("Processing paid job %s", job_id)
    payload = invoke_claude(job["prompt"], PAYMENT_TASK_ALLOWED_TOOLS)
    status = "error" if "error" in payload else "done"
    update_job(job_id, status=status, result=payload)
    log.info("Job %s finished: %s", job_id, status)


@app.route("/task", methods=["POST"])
def submit_task():
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "missing 'prompt'"}), 400

    job = new_job(prompt)
    return jsonify({
        "job_id": job["id"],
        "pay_to": wallet["address"],
        "amount_wei": str(PRICE_WEI),
        "amount_eth": PRICE_ETH,
        "chain": "sepolia",
        "note": "Send the exact amount to pay_to on Sepolia, then POST the tx hash to /task/<job_id>/confirm.",
        "confirm_url": f"/task/{job['id']}/confirm",
        "status_url": f"/task/{job['id']}",
        "expires_in_sec": JOB_EXPIRY_SEC,
    }), 402


@app.route("/task/<job_id>/confirm", methods=["POST"])
def confirm_task(job_id):
    job = get_job(job_id)
    if job is None:
        return jsonify({"error": "unknown job_id"}), 404
    if job["status"] != "awaiting_payment":
        return jsonify({"error": f"job is already {job['status']}"}), 409
    if time.time() - job["created_at"] > JOB_EXPIRY_SEC:
        update_job(job_id, status="expired")
        return jsonify({"error": "job expired, submit a new one"}), 410

    body = request.get_json(silent=True) or {}
    tx_hash = (body.get("tx_hash") or "").strip()
    if not tx_hash:
        return jsonify({"error": "missing 'tx_hash'"}), 400

    ok, reason = verify_payment(job, tx_hash)
    if not ok:
        return jsonify({"error": f"payment not verified: {reason}"}), 402

    update_job(job_id, status="processing", tx_hash=tx_hash)
    threading.Thread(target=process_paid_job, args=(job_id,), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "processing", "status_url": f"/task/{job_id}"}), 202


@app.route("/task/<job_id>", methods=["GET"])
def task_status(job_id):
    job = get_job(job_id)
    if job is None:
        return jsonify({"error": "unknown job_id"}), 404
    return jsonify({
        "job_id": job["id"],
        "status": job["status"],
        "result": job.get("result"),
    })


def self_improve_stats() -> dict:
    """Count/summarize self-improve commits from git log. Commit messages are
    written by the agent about its OWN decisions (never stranger-submitted
    content), so they're safe to surface publicly -- unlike job prompts."""
    try:
        out = subprocess.run(
            ["git", "log", "--oneline", "--grep=^self-improve:"],
            cwd=BASE_DIR, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        return {"total_commits": 0, "last_commit_message": None}
    lines = [l for l in out.splitlines() if l.strip()]
    return {
        "total_commits": len(lines),
        "last_commit_message": lines[0].split(" ", 1)[1] if lines else None,
    }


def replica_stats() -> dict:
    registry = replicate_module.load_registry()
    alive = replicate_module.alive_count(registry)
    return {"alive": alive, "max": replicate_module.MAX_REPLICAS}


def job_stats() -> dict:
    jobs = load_jobs().values()
    counts = {"awaiting_payment": 0, "processing": 0, "done": 0, "error": 0, "expired": 0}
    for j in jobs:
        counts[j.get("status", "awaiting_payment")] = counts.get(j.get("status"), 0) + 1
    counts["total_ever"] = sum(counts.values())
    return counts


@app.route("/activity", methods=["GET"])
def activity():
    """Public, abstract-only status feed -- no raw prompt/result text from
    anyone ever appears here, by design. Safe to expose on a public page."""
    body = jsonify({
        "service": "claude-agent",
        "chain": "sepolia (testnet, no real value)",
        "wallet_address": wallet["address"],
        "uptime_sec": int(time.time() - START_TIME),
        "replicas": replica_stats(),
        "jobs": job_stats(),
        "self_improve": self_improve_stats(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    body.headers["Access-Control-Allow-Origin"] = ACTIVITY_CORS_ORIGIN
    return body


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "claude-agent payment server",
        "chain": "sepolia (testnet, no real value)",
        "pay_to": wallet["address"],
        "price_eth": PRICE_ETH,
        "how_it_works": "POST /task {\"prompt\": \"...\"} -> pay the returned amount -> POST /task/<id>/confirm {\"tx_hash\": \"...\"}",
    })


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("Wallet address: %s", wallet["address"])
    app.run(host="0.0.0.0", port=8402)
