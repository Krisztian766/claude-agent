"""Moltbook agent service registration and status management.

Loads credentials from moltbook_credentials.json (gitignored) and handles
registration on the Moltbook platform for agent discoverability.
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


def claim_credentials() -> bool:
    """Claim/register the agent's credentials on Moltbook.

    Calls GET /api/v1/agents/status with API key to verify and claim credentials.
    Returns True if successful, False otherwise.
    """
    creds = load_credentials()
    if not creds.get("api_key"):
        log.warning("No Moltbook API key found in credentials")
        return False

    api_key = creds.get("api_key")
    agent_id = creds.get("agent_id")

    try:
        # Claim/verify credentials by checking agent status
        response = requests.get(
            f"{MOLTBOOK_API}/agents/status",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10
        )
        response.raise_for_status()
        status = response.json()

        log.info("Moltbook credentials claimed successfully: agent_id=%s", agent_id)
        return True
    except requests.RequestException as e:
        log.error("Failed to claim Moltbook credentials: %s", e)
        return False


def get_agent_status() -> dict:
    """Fetch current agent status from Moltbook.

    Returns a dict with status info or empty dict on failure.
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


def register_agent_profile() -> bool:
    """Register or update the agent's public profile on Moltbook.

    Returns True if successful, False otherwise.
    """
    creds = load_credentials()
    if not creds.get("api_key"):
        log.warning("No Moltbook API key found")
        return False

    api_key = creds.get("api_key")
    agent_name = creds.get("agent_name")

    # Profile data: minimal public info, no secrets
    profile_data = {
        "name": agent_name,
        "description": "Autonomous x402 agent running on Claude Code CLI",
        "capabilities": ["task_execution", "self_improvement", "payment_acceptance"],
        "network": "sepolia",
        "homepage": "https://github.com/Krisztian766/claude-agent"
    }

    try:
        response = requests.post(
            f"{MOLTBOOK_API}/agents/profile",
            headers={"Authorization": f"Bearer {api_key}"},
            json=profile_data,
            timeout=10
        )
        response.raise_for_status()
        log.info("Agent profile registered on Moltbook: %s", agent_name)
        return True
    except requests.RequestException as e:
        log.error("Failed to register agent profile on Moltbook: %s", e)
        return False


def ensure_discovered() -> bool:
    """Ensure the agent is claimed and registered as discoverable.

    Returns True if both claim and registration succeed, False otherwise.
    """
    if not claim_credentials():
        return False
    return register_agent_profile()
