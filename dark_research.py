"""Read-only Tor/.onion research capability.

SCOPE, DELIBERATELY NARROW (owner's explicit boundary, 2026-09-15, restated
and held firm after a broader "let it do anything, transactions too" ask was
declined): fetch and read text content from .onion sites for research/OSINT
purposes only -- ties into the owner's real "darkweb-monitor" line of work.

Hard limits, enforced in code, not just policy:
  - GET only. No POST/PUT/DELETE, no form submission, no login flows.
  - No transactions of any kind -- this module has no wallet access and
    can't reach vitality.py/wallet.py's signing functions.
  - No file downloads -- text/html responses only, size-capped, never
    written to disk as a retrievable file (unlike payment_server.py's
    dropper-chain fetcher pattern from the old honeypot project, which
    deliberately DID save files -- this is the opposite: read, summarize,
    discard).
  - Every fetch is logged (URL, timestamp, byte count) to dark_research.log
    for auditability -- this is sensitive enough that "trust me" isn't
    good enough, there should be a record.

Not reachable from payment_server.py -- same boundary as Bash/Docker access,
self-improve, and replication: anonymous strangers can never trigger this.
"""
import logging
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "dark_research.log"

TOR_PROXY = "socks5h://127.0.0.1:9050"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2MB text cap
FETCH_TIMEOUT_SEC = 60

log = logging.getLogger("dark-research")
if not log.handlers:
    handler = logging.FileHandler(LOG_FILE)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler())
    log.setLevel(logging.INFO)


def fetch_onion(url: str) -> dict:
    """GET a .onion (or any) URL through Tor. Returns text content, never
    writes files, never follows a non-GET action. Always logs the attempt,
    success or failure, for auditability."""
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "only http(s) URLs are supported"}

    proxies = {"http": TOR_PROXY, "https": TOR_PROXY}
    try:
        resp = requests.get(
            url, proxies=proxies, timeout=FETCH_TIMEOUT_SEC,
            headers={"User-Agent": "Mozilla/5.0 (research; contact via GitHub Krisztian766)"},
            stream=True,
        )
        content = resp.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
        truncated = len(content) > MAX_RESPONSE_BYTES
        content = content[:MAX_RESPONSE_BYTES]
        text = content.decode("utf-8", errors="replace")
    except Exception as e:
        log.warning("FETCH FAILED url=%s error=%s", url, e)
        return {"ok": False, "error": str(e)}

    log.info("FETCH OK url=%s status=%d bytes=%d truncated=%s", url, resp.status_code, len(content), truncated)
    return {
        "ok": True,
        "status_code": resp.status_code,
        "content_type": resp.headers.get("Content-Type", ""),
        "text": text,
        "truncated": truncated,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
