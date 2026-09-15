"""Customer outreach drafting: the agent writes its own pitch for the
payment-gated task service, based on real current stats.

SAFETY BOUNDARY (same pattern as payment_server.py / self_improve.py's own
boundaries -- do not relax without a real conversation): this module can
only WRITE LOCAL DRAFT FILES. It has no social-media/API credentials, no
Bash, no network-posting capability of any kind. A draft only ever reaches
the outside world if the owner reads it and posts it themselves. Nothing
here is autonomous-publish -- that is a deliberate, permanent limit, not a
todo.
"""
import json
import time
from pathlib import Path

from claude_client import invoke_claude

BASE_DIR = Path(__file__).resolve().parent
DRAFTS_DIR = BASE_DIR / "outreach" / "drafts"
JOBS_PATH = BASE_DIR / "payment_jobs.json"

# Read-only on its own stats/README; no Bash, no Write outside drafts/ (the
# prompt constrains where it writes, there is no filesystem-level sandbox
# beyond that -- same caveat as elsewhere in this project).
OUTREACH_TOOLS = "Read Grep Glob"


def load_jobs() -> dict:
    if not JOBS_PATH.exists():
        return {}
    try:
        return json.loads(JOBS_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def stats_summary() -> dict:
    jobs = load_jobs().values()
    done = sum(1 for j in jobs if j.get("status") == "done")
    total = len(list(jobs))
    return {"jobs_done": done, "jobs_total": total}


def draft_outreach() -> dict:
    stats = stats_summary()
    prompt = (
        "You run an autonomous agent that does paid work via a Sepolia "
        "testnet x402-style payment gate (see README.md and payment_server.py "
        "in this directory for how it actually works). So far it has "
        f"completed {stats['jobs_done']} of {stats['jobs_total']} submitted jobs. "
        "Write a short, honest outreach post (150-300 words) to attract people "
        "who'd want to try submitting a task. Be upfront that payment is "
        "Sepolia TESTNET ETH with no real monetary value -- this is a public "
        "demo/showcase of an autonomous agent, not a paid commercial service. "
        "Don't oversell or use hype language. Explain concretely how someone "
        "would try it (the POST /task flow). Target audience: developers "
        "interested in AI agents, crypto/x402 payment rails, or autonomous "
        "systems. Reply with ONLY the post text, nothing else."
    )
    payload = invoke_claude(prompt, OUTREACH_TOOLS)
    if "error" in payload:
        return {"drafted": False, "reason": payload["error"]}

    text = (payload.get("result") or "").strip()
    if not text:
        return {"drafted": False, "reason": "empty response"}

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    draft_path = DRAFTS_DIR / f"{ts}.txt"
    draft_path.write_text(text + "\n")

    return {"drafted": True, "path": str(draft_path), "text": text, "stats": stats}
