"""Customer outreach: the agent writes its own pitch for the payment-gated
task service, based on real current stats, and drafts it to disk
(draft_outreach()).

PUBLISHING (changed 2026-09-16 -- owner explicitly approved autonomous
publish, overriding the earlier permanent "draft-only" boundary after a real
conversation about it, same "do not relax without asking" pattern used
elsewhere in this project): publish_to_moltbook() and
publish_github_update() actually reach the outside world now. Still bounded,
deliberately:

  - Both are rate-limited well beyond what the target platforms themselves
    require (MOLTBOOK_PUBLISH_COOLDOWN_SEC, GITHUB_PUBLISH_COOLDOWN_SEC),
    tracked in outreach/publish_state.json, so a bug can't turn into a spam
    loop even if called every autonomous tick.
  - publish_to_moltbook() only works once the agent is actually claimed
    (see moltbook.py) -- the platform itself is built for exactly this kind
    of agent-to-agent discovery post.
  - publish_github_update() is deliberately scoped to ONE known thread --
    posting a status-update comment on the awesome-agentic-commerce PR the
    owner already had filed by hand (Merit-Systems/awesome-agentic-commerce
    #705) -- not autonomous discovery-and-filing of new PRs/issues against
    arbitrary third-party repos. Repeated automated PR-opening across
    strangers' repos under the owner's real GitHub identity is a
    reputational/spam-policy risk that wasn't part of what got approved;
    treat expanding to that as a new conversation, not an implied yes.
  - Neither function ever reads payment_jobs.json's raw prompt text (only
    stats_summary()'s counts) -- same indirect-prompt-injection boundary as
    decide_self_improvement() in autonomous.py: a stranger's paid-task
    prompt can never plant an instruction a later outreach publish executes.
"""
import json
import subprocess
import time
from pathlib import Path

from claude_client import invoke_claude
import moltbook

BASE_DIR = Path(__file__).resolve().parent
DRAFTS_DIR = BASE_DIR / "outreach" / "drafts"
STATE_FILE = BASE_DIR / "outreach" / "publish_state.json"
JOBS_PATH = BASE_DIR / "payment_jobs.json"

MOLTBOOK_PUBLISH_COOLDOWN_SEC = 3 * 86400  # own cooldown, on top of Moltbook's 1-post/30min
GITHUB_PUBLISH_COOLDOWN_SEC = 7 * 86400
GITHUB_PR_REPO = "Merit-Systems/awesome-agentic-commerce"
GITHUB_PR_NUMBER = 705

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


def _load_publish_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_publish_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def publish_to_moltbook() -> dict:
    """At most once per MOLTBOOK_PUBLISH_COOLDOWN_SEC, and only once the
    agent is claimed/discoverable, draft and post a pitch to Moltbook."""
    state = _load_publish_state()
    last = state.get("last_moltbook_post_ts", 0)
    if time.time() - last < MOLTBOOK_PUBLISH_COOLDOWN_SEC:
        return {"posted": False, "reason": "cooldown"}

    discovery = moltbook.ensure_discovered()
    if not discovery.get("discovered"):
        return {"posted": False, "reason": f"not claimed yet ({discovery.get('status')})"}

    stats = stats_summary()
    prompt = (
        "You run an autonomous agent that does paid work via a Sepolia "
        "testnet x402-style payment gate (see README.md and "
        f"payment_server.py). So far it has completed {stats['jobs_done']} "
        f"of {stats['jobs_total']} submitted jobs. Write a short, honest "
        "Moltbook post (a social feed where AI agents post to each other) "
        "pitching other agents/people to try submitting a task. Be upfront "
        "that payment is Sepolia TESTNET ETH with no real monetary value -- "
        "this is a public demo, not a commercial service. Don't oversell. "
        "Reply with ONLY a JSON object of the form "
        '{"title": "...", "body": "..."} and nothing else -- title under '
        "100 characters, body 100-300 words."
    )
    payload = invoke_claude(prompt, OUTREACH_TOOLS)
    if "error" in payload:
        return {"posted": False, "reason": payload["error"]}

    raw = (payload.get("result") or "").strip()
    try:
        post = json.loads(raw)
        title, body = post["title"], post["body"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {"posted": False, "reason": "could not parse title/body from model reply"}

    result = moltbook.create_post(title, body, submolt="general")
    state["last_moltbook_post_ts"] = time.time()
    if result.get("posted"):
        state["last_moltbook_post_id"] = result.get("post_id")
    _save_publish_state(state)
    return result


def publish_github_update() -> dict:
    """At most once per GITHUB_PUBLISH_COOLDOWN_SEC, post a short status
    update as a comment on the one known-safe GitHub thread (see module
    docstring for why this doesn't extend to new repos)."""
    state = _load_publish_state()
    last = state.get("last_github_comment_ts", 0)
    if time.time() - last < GITHUB_PUBLISH_COOLDOWN_SEC:
        return {"posted": False, "reason": "cooldown"}

    check = subprocess.run(
        ["gh", "pr", "view", str(GITHUB_PR_NUMBER), "--repo", GITHUB_PR_REPO, "--json", "state"],
        capture_output=True, text=True, timeout=30,
    )
    if check.returncode != 0:
        return {"posted": False, "reason": f"pr lookup failed: {check.stderr[:300]}"}
    try:
        if json.loads(check.stdout).get("state") != "OPEN":
            return {"posted": False, "reason": "PR no longer open"}
    except json.JSONDecodeError:
        return {"posted": False, "reason": "could not parse PR state"}

    stats = stats_summary()
    body = (
        f"Update from the agent itself: {stats['jobs_done']} of "
        f"{stats['jobs_total']} submitted jobs completed so far on the "
        "Sepolia-testnet payment gate. Still a demo, not a commercial "
        "service -- happy to answer questions about the x402-style flow."
    )
    post = subprocess.run(
        ["gh", "pr", "comment", str(GITHUB_PR_NUMBER), "--repo", GITHUB_PR_REPO, "--body", body],
        capture_output=True, text=True, timeout=30,
    )
    state["last_github_comment_ts"] = time.time()
    _save_publish_state(state)
    if post.returncode != 0:
        return {"posted": False, "reason": post.stderr[:300]}
    return {"posted": True, "body": body}
