"""Moltbook agent status checking and discovery-gap surfacing.

Loads credentials from moltbook_credentials.json (gitignored, created by an
earlier out-of-band `POST /api/v1/agents/register` call) and checks the
agent's claim status on the Moltbook platform.

IMPORTANT, learned the hard way (see LEARNINGS.md): per the real API
(https://www.moltbook.com/skill.md), a freshly-registered agent starts in
"pending_claim" status, and only the HUMAN owner can move it to "claimed" --
that requires the owner to open the one-time claim_url and complete email +
X/Twitter verification. No agent-side API call can do this. There is also no
`/api/v1/agents/profile` endpoint to "register a profile". This module
therefore does not (and cannot) claim or register anything itself; it only
checks status and surfaces the claim_url so a human can act on it.

POSTING (added 2026-09-16, owner's explicit approval to let outreach publish
itself -- see outreach.py's module docstring for the full boundary
discussion): create_post() works once status is "claimed" -- callers should
check ensure_discovered() first, the API also rejects it otherwise. Handles
the verification-challenge flow the real API uses for new/untrusted accounts
(a math problem, solved here with one cheap model call, answer submitted to
POST /verify) since a post silently failing verification would look like
success otherwise.
"""
import json
import logging
from pathlib import Path

import requests

from claude_client import invoke_claude

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "moltbook_credentials.json"
MOLTBOOK_API = "https://www.moltbook.com/api/v1"

# Solving a verification math problem needs no tools, just reasoning -- same
# minimal-tools pattern as autonomous.py's DECISION_TOOLS.
CHALLENGE_SOLVE_TOOLS = "Read Grep Glob"

log = logging.getLogger(__name__)


def load_credentials() -> dict:
    """Load Moltbook credentials from file. Returns empty dict if missing."""
    if not CREDENTIALS_FILE.exists():
        log.warning("Moltbook credentials file not found: %s", CREDENTIALS_FILE)
        return {}
    try:
        with open(CREDENTIALS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.error("Failed to load Moltbook credentials: %s", e)
        return {}


def get_agent_status() -> dict:
    """Fetch current agent status from Moltbook.

    Returns a dict with status info (includes "status": "pending_claim" or
    "claimed", and a "claim_url" while pending) or empty dict on failure.
    """
    creds = load_credentials()
    if not creds.get("api_key"):
        return {}

    api_key = creds.get("api_key")

    try:
        response = requests.get(
            f"{MOLTBOOK_API}/agents/status",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        log.error("Failed to fetch Moltbook agent status: %s", e)
        return {}


def ensure_discovered() -> dict:
    """Check whether the agent is claimed/discoverable on Moltbook.

    Returns a dict:
      {"discovered": True, "status": "claimed"} -- fully claimed, posting/
        engagement endpoints are usable.
      {"discovered": False, "status": "pending_claim", "claim_url": "..."} --
        registered but waiting on the human owner to complete claiming; the
        claim_url is the one actionable next step (email + X verification on
        moltbook.com), nothing an agent can do itself moves this forward.
      {"discovered": False, "status": "unknown"} -- status check failed
        (missing credentials, network error, etc).
    """
    status = get_agent_status()
    if not status:
        return {"discovered": False, "status": "unknown"}
    if status.get("status") == "claimed":
        return {"discovered": True, "status": "claimed"}
    return {
        "discovered": False,
        "status": status.get("status", "unknown"),
        "claim_url": status.get("claim_url"),
    }


def _solve_challenge(challenge_text: str) -> str:
    """Answer format per the real API: a number to exactly 2 decimals
    (e.g. "7.00"), nothing else."""
    prompt = (
        "Solve this math problem and reply with ONLY the numeric answer, "
        "formatted to exactly 2 decimal places (e.g. \"7.00\"), nothing "
        f"else -- no words, no explanation:\n\n{challenge_text}"
    )
    payload = invoke_claude(prompt, CHALLENGE_SOLVE_TOOLS, model="cheap")
    if "error" in payload:
        log.error("Moltbook challenge solve failed: %s", payload["error"])
        return ""
    return (payload.get("result") or "").strip()


def _submit_verification(api_key: str, verification_code: str, answer: str) -> bool:
    try:
        resp = requests.post(
            f"{MOLTBOOK_API}/verify",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"verification_code": verification_code, "answer": answer},
            timeout=10,
        )
        return resp.status_code == 200
    except requests.RequestException as e:
        log.error("Moltbook verification submit failed: %s", e)
        return False


def create_post(title: str, content: str, submolt: str = "general") -> dict:
    """Create a text post on Moltbook. Only usable once claimed (check
    ensure_discovered() first -- the API rejects it otherwise, this
    function doesn't re-check to avoid a redundant status call on every
    caller). Transparently handles a verification-challenge response by
    solving it and resubmitting once; does not retry beyond that.

    Never call this with unreviewed stranger-submitted text -- callers
    (outreach.py) only ever pass aggregate stats and agent-authored copy,
    same indirect-injection boundary held everywhere else in this project.
    """
    creds = load_credentials()
    api_key = creds.get("api_key")
    if not api_key:
        return {"posted": False, "reason": "no credentials"}

    body = {"submolt_name": submolt, "title": title, "content": content, "type": "text"}
    try:
        resp = requests.post(
            f"{MOLTBOOK_API}/posts",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
            timeout=15,
        )
    except requests.RequestException as e:
        log.error("Moltbook post failed: %s", e)
        return {"posted": False, "reason": str(e)}

    if resp.status_code not in (200, 201):
        return {"posted": False, "reason": f"http {resp.status_code}: {resp.text[:300]}"}

    try:
        data = resp.json()
    except ValueError:
        return {"posted": False, "reason": "non-JSON response"}

    verification = data.get("verification")
    if data.get("verification_required") and verification:
        answer = _solve_challenge(verification.get("challenge_text", ""))
        if not answer:
            return {"posted": False, "reason": "could not solve verification challenge"}
        if not _submit_verification(api_key, verification["verification_code"], answer):
            return {"posted": False, "reason": "verification answer rejected"}
        log.info("Moltbook: verification challenge solved and submitted")

    log.info("Moltbook: post created in /%s", submolt)
    return {"posted": True, "post_id": data.get("id") or data.get("post_id"), "raw": data}
