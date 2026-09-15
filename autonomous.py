"""Autonomous orchestrator: the agent decides FOR ITSELF, on a schedule,
whether to replicate and/or self-improve. This is the layer that makes the
owner-triggered self_improve.py/replicate.py primitives actually autonomous.

Design intent (owner's explicit request, 2026-09-15): "replicate only if
useful, creates real value", "self-improve freely, whenever it wants to
evolve" -- translated into concrete, bounded rules, since neither can be
literally unbounded without risking runaway subscription usage or an
unstable self-edit loop:

- Replicate: triggered by the only real, measurable "value" signal that
  exists in this system -- paid demand. If more than
  REPLICATE_BACKLOG_THRESHOLD payment_server jobs are concurrently
  "processing" at once, that's real, external evidence that replicating
  would let it do more useful work, so it does. Still hard-capped by
  replicate.py's MAX_REPLICAS/MAX_DEPTH regardless.
- Self-improve: the agent has completely free choice of WHETHER and WHAT,
  every tick -- nothing here tells it what to improve. What's bounded is the
  tick rate (ORCHESTRATOR_TICK_SEC), so "freely, whenever it wants" can't
  become a tight loop burning real subscription usage; and a cheap
  Read-only "is there anything concrete?" pre-check runs before ever paying
  for a full, expensive self-improve cycle.

Every decision -- act or deliberately don't -- is logged to autonomous.log,
so this stays observable rather than a black box. See README "Nyomon
követés".
"""
import json
import logging
import time
from pathlib import Path

from claude_client import invoke_claude
import self_improve
import replicate as replicate_module
import outreach as outreach_module

BASE_DIR = Path(__file__).resolve().parent
JOBS_PATH = BASE_DIR / "payment_jobs.json"
LOG_FILE = BASE_DIR / "autonomous.log"

REPLICATE_BACKLOG_THRESHOLD = 3
ORCHESTRATOR_TICK_SEC = 1800  # 30 min
OUTREACH_MIN_INTERVAL_SEC = 86400  # at most once/day -- drafting still costs a real call
DECISION_TOOLS = "Read Grep Glob"

log = logging.getLogger("claude-agent-autonomous")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
    )


def load_jobs() -> dict:
    if not JOBS_PATH.exists():
        return {}
    try:
        return json.loads(JOBS_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def processing_backlog() -> int:
    jobs = load_jobs()
    return sum(1 for j in jobs.values() if j.get("status") == "processing")


def maybe_replicate() -> dict:
    backlog = processing_backlog()
    if backlog < REPLICATE_BACKLOG_THRESHOLD:
        log.info("Replikáció-vizsgálat: torlódás=%d, küszöb=%d, nem replikál", backlog, REPLICATE_BACKLOG_THRESHOLD)
        return {"replicated": False, "reason": "backlog below threshold", "backlog": backlog}

    log.info("Replikáció-vizsgálat: torlódás=%d >= küszöb=%d, replikál", backlog, REPLICATE_BACKLOG_THRESHOLD)
    result = replicate_module.spawn_replica()
    log.info("Replikáció eredménye: %s", result)
    return result


def decide_self_improvement() -> str:
    """Cheap, Read-only call: does the agent see something concretely worth
    improving about itself right now? Returns an instruction, or "" if not.
    Complete editorial freedom on WHAT -- nothing here suggests a topic.

    Deliberately does NOT read payment_jobs.json -- that file holds raw
    prompt text submitted by anonymous strangers via payment_server.py. Now
    that self_improve.py has Bash access (see self_improve.py), letting this
    decision step read attacker-controlled text would be a real indirect
    prompt-injection path: a stranger could craft a paid task prompt that
    plants an instruction here, which a later self-improve cycle could then
    execute for real. agent.log/autonomous.log are safe -- they're written
    by this agent's own code, never by echoing stranger input verbatim."""
    prompt = (
        "You are reviewing your own recent operation. Look at agent.log and "
        "autonomous.log in this directory (if they exist) to see what "
        "you've actually been doing. "
        "If you see a concrete, worthwhile improvement to your own code "
        "(a real bug, a missing safeguard, a clear inefficiency, a genuinely "
        "useful small feature) reply with ONLY a one-sentence instruction "
        "describing it. If nothing concrete stands out, reply with exactly: "
        "NONE. Don't invent busywork just to have something to say."
    )
    payload = invoke_claude(prompt, DECISION_TOOLS)
    if "error" in payload:
        log.warning("Self-improve döntési hívás sikertelen: %s", payload["error"])
        return ""
    text = (payload.get("result") or "").strip()
    if not text or text.upper() == "NONE":
        return ""
    return text


def maybe_self_improve() -> dict:
    instruction = decide_self_improvement()
    if not instruction:
        log.info("Self-improve-vizsgálat: az agent nem talált konkrét javítanivalót magán")
        return {"applied": False, "reason": "agent decided nothing concrete to improve"}

    log.info("Self-improve-vizsgálat: az agent ezt döntötte: %s", instruction)
    result = self_improve.self_improve(instruction)
    log.info(
        "Self-improve eredménye: applied=%s reason=%s commit=%s",
        result.get("applied"), result.get("reason"), result.get("commit"),
    )
    return result


def maybe_draft_outreach() -> dict:
    """At most once/day, draft a fresh outreach post -- see outreach.py for
    the hard boundary (draft-only, never auto-published)."""
    drafts = sorted(outreach_module.DRAFTS_DIR.glob("*.txt")) if outreach_module.DRAFTS_DIR.exists() else []
    if drafts:
        age = time.time() - drafts[-1].stat().st_mtime
        if age < OUTREACH_MIN_INTERVAL_SEC:
            log.info("Outreach-vizsgálat: legutóbbi piszkozat %ds ezelőtt, még nem esedékes új", int(age))
            return {"drafted": False, "reason": "too soon since last draft"}

    log.info("Outreach-vizsgálat: új kiajánlás-piszkozat írása")
    result = outreach_module.draft_outreach()
    log.info("Outreach eredménye: drafted=%s path=%s", result.get("drafted"), result.get("path"))
    return result


def tick() -> dict:
    log.info("Autonóm ciklus indul")
    replicate_result = maybe_replicate()
    improve_result = maybe_self_improve()
    outreach_result = maybe_draft_outreach()
    log.info("Autonóm ciklus vége")
    return {"replicate": replicate_result, "self_improve": improve_result, "outreach": outreach_result}


def run_forever() -> None:
    setup_logging()
    log.info("Autonóm orchestrátor elindult (ciklus %d másodpercenként)", ORCHESTRATOR_TICK_SEC)
    while True:
        try:
            tick()
        except Exception:
            log.exception("Kezeletlen hiba az autonóm ciklusban, folytatás")
        time.sleep(ORCHESTRATOR_TICK_SEC)


if __name__ == "__main__":
    run_forever()
