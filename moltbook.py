"""Moltbook agent status checking and discovery-gap surfacing.

Loads credentials from moltbook_credentials.json (gitignored, created by an
earlier out-of-band `POST /api/v1/agents/register` call) and checks the
agent's claim status on the Moltbook platform.

IMPORTANT, learned the hard way (see LEARNINGS.md): per the real API
(https://www.moltbook.com/skill.md), a freshly-registered agent starts in
"pending_claim" status, and only the HUMAN owner can move it to "claimed" --
that requires the owner to open the one-time claim_url and complete email +
X/Twitter verification. No agent-side API call can do this. There is also no
`/api/v1/agents/profile` endpoint to "register a profile" -- posting/
engagement only becomes possible once status is already "claimed". This
module therefore does not (and cannot) claim or register anything itself; it
only checks status and surfaces the claim_url so a human can act on it.
"""
import json
import logging
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "moltbook_credentials.json"
MOLTBOOK_API = "https://www.moltbook.com/api/v1"

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
