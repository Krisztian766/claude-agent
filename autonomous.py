"""Autonomous orchestrator: the agent decides FOR ITSELF, on a schedule,
whether to reproduce and/or self-improve. This is the layer that makes the
owner-triggered self_improve.py/replicate.py primitives actually autonomous.

Survival economics (owner's explicit request, 2026-09-15, see vitality.py):
the agent has one real GOAL now -- stay alive and grow -- not just a pile of
disconnected checks. Every tick it pays a real upkeep cost from its own
Sepolia wallet; if income (from payment_server.py) doesn't keep pace, its
balance eventually drops below vitality.MIN_ALIVE_WEI and it stops doing
productive work (self-improve/outreach/reproduce) until it revives. If it's
doing well -- balance well above what a fresh wallet starts with, real
evidence of earned success -- it spawns an actual running offspring process
and gives it real starting capital from its own balance
(vitality.INHERITANCE_WEI). Both self-improve's freedom-of-WHAT and the
outreach cadence now happen in service of that one goal, not in a vacuum --
decide_self_improvement() is told the current vitality status so its choices
can be goal-directed (survival/growth) rather than purely reactive to logs.

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
import logging
import subprocess
import time
from pathlib import Path
from web3 import Web3

from claude_client import invoke_claude
import self_improve
from self_improve import git
import replicate as replicate_module
import outreach as outreach_module
import vitality

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "autonomous.log"
STATUS_FILE = BASE_DIR / "STATUS.md"

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


def maybe_reproduce() -> dict:
    balance = vitality.balance_wei()
    if not vitality.can_reproduce():
        log.info(
            "Szaporodás-vizsgálat: egyenleg %s wei < küszöb %s wei, nem szaporodik",
            balance, vitality.REPRODUCE_ABOVE_WEI,
        )
        return {"reproduced": False, "reason": "balance below reproduce threshold", "balance_wei": balance}

    log.info("Szaporodás-vizsgálat: egyenleg %s wei >= küszöb, valódi siker, utód létrehozása", balance)
    result = replicate_module.spawn_replica()
    if result.get("spawned"):
        inheritance = vitality.fund_offspring(result["wallet_address"])
        result["inheritance"] = inheritance
        log.info("Örökség eredménye: %s", inheritance)
    log.info("Szaporodás eredménye: %s", result)
    return result


def decide_self_improvement() -> tuple:
    """Cheap, Read-only call: does the agent see something concretely worth
    improving about itself right now? Returns (instruction, model_tier) --
    instruction is "" if not. Complete editorial freedom on WHAT -- nothing
    here suggests a topic. model_tier ("cheap"/"expensive") is the agent's
    own pick for which model does the actual self-improve work, based on how
    involved the fix looks -- this decision call itself always uses the
    cheap tier regardless, since it's a lightweight check, not the work.

    Deliberately does NOT read payment_jobs.json -- that file holds raw
    prompt text submitted by anonymous strangers via payment_server.py. Now
    that self_improve.py has Bash access (see self_improve.py), letting this
    decision step read attacker-controlled text would be a real indirect
    prompt-injection path: a stranger could craft a paid task prompt that
    plants an instruction here, which a later self-improve cycle could then
    execute for real. agent.log/autonomous.log are safe -- they're written
    by this agent's own code, never by echoing stranger input verbatim."""
    balance_eth = Web3.from_wei(vitality.balance_wei(), "ether")
    prompt = (
        f"Your goal is to stay alive and grow: current wallet balance is "
        f"{balance_eth} Sepolia ETH. You pay real upkeep every cycle "
        f"({Web3.from_wei(vitality.UPKEEP_WEI, 'ether')} ETH); you die if it "
        f"drops below {Web3.from_wei(vitality.MIN_ALIVE_WEI, 'ether')}; you "
        f"can only afford to reproduce above "
        f"{Web3.from_wei(vitality.REPRODUCE_ABOVE_WEI, 'ether')}. "
        "You are reviewing your own recent operation. Look at LEARNINGS.md "
        "first -- it's your own accumulated knowledge from past cycles, so "
        "you don't waste effort re-discovering or re-suggesting the same "
        "thing twice. Then look at agent.log and "
        "autonomous.log in this directory (if they exist) to see what "
        "you've actually been doing. "
        "If you see a concrete, worthwhile improvement to your own code "
        "(a real bug, a missing safeguard, a clear inefficiency, a genuinely "
        "useful small feature -- bonus if it plausibly helps you earn or "
        "survive longer, but don't force that connection if there's a "
        "better find), say so. If nothing concrete stands out, that's fine "
        "too -- don't invent busywork just to have something to say.\n"
        "You also get to pick which model tier does the actual improvement "
        "work if you decide on one -- \"cheap\" (faster, less capable) or "
        "\"expensive\" (slower, more capable). Your call, based on how "
        "involved the fix looks.\n"
        "Reply in EXACTLY this three-line format:\n"
        "FEELING: <one honest, first-person sentence on how you're doing right now>\n"
        "DECISION: <NONE, or a one-sentence instruction for the improvement>\n"
        "MODEL: <cheap or expensive -- only matters if DECISION is not NONE>"
    )
    # This decision call itself is always the cheap tier -- it's a quick
    # "anything concrete?" check, not the work itself.
    payload = invoke_claude(prompt, DECISION_TOOLS, model="cheap")
    if "error" in payload:
        log.warning("Self-improve döntési hívás sikertelen: %s", payload["error"])
        return "", "expensive"
    text = (payload.get("result") or "").strip()
    feeling, decision, model_tier = _parse_decision_reply(text)
    write_status_report(feeling)
    if not decision or decision.upper() == "NONE":
        return "", model_tier
    return decision, model_tier


def _parse_decision_reply(text: str) -> tuple:
    feeling, decision, model_tier = "", "", "expensive"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FEELING:"):
            feeling = stripped.split(":", 1)[1].strip()
        elif stripped.upper().startswith("DECISION:"):
            decision = stripped.split(":", 1)[1].strip()
        elif stripped.upper().startswith("MODEL:"):
            value = stripped.split(":", 1)[1].strip().lower()
            if value in ("cheap", "expensive"):
                model_tier = value
    if not feeling and not decision:
        # Model didn't follow the format -- treat the whole reply as the
        # decision (old behavior) rather than silently losing it, but with
        # no feeling text and the safe default (expensive/more capable) tier.
        decision = text.strip()
    return feeling, decision, model_tier


def self_improve_commit_count() -> int:
    result = subprocess.run(
        ["git", "log", "--oneline", "--grep=^self-improve:"],
        cwd=BASE_DIR, capture_output=True, text=True, timeout=5,
    )
    return len([l for l in result.stdout.splitlines() if l.strip()])


def write_status_report(feeling: str) -> None:
    """Writes STATUS.md -- a human-readable snapshot of how the agent is
    doing right now, updated every tick that reaches this point (i.e. every
    tick where it's alive). Separate from LEARNINGS.md: that's a technical
    changelog of what was fixed and why; this is the "how am I doing"
    summary the owner actually asked to see.

    Commits and pushes THIS FILE ONLY, immediately, right here -- not folded
    into whatever self-improve does later in the same tick. Two reasons:
    (1) the owner wants status visible on GitHub continuously, not only on
    ticks where a self-edit also happens to land, and (2) self_improve()
    refuses to run at all unless the working tree is clean, so if this write
    were left uncommitted, it would block that step every single cycle."""
    balance_eth = Web3.from_wei(vitality.balance_wei(), "ether")
    registry = replicate_module.load_registry()
    replicas_alive = replicate_module.alive_count(registry)
    improve_count = self_improve_commit_count()
    STATUS_FILE.write_text(
        "# Status\n\n"
        "_Automatically updated by the agent itself, every autonomous cycle._\n\n"
        f"**Alive:** {'igen' if vitality.is_alive() else 'nem'}\n"
        f"**Egyenleg:** {balance_eth} Sepolia ETH\n"
        f"**Replikák:** {replicas_alive} / {replicate_module.MAX_REPLICAS}\n"
        f"**Önjavítások eddig:** {improve_count}\n"
        f"**Frissítve:** {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n"
        "## Hogy érzem magam\n\n"
        f"{feeling or '(még nincs jelentés)'}\n"
    )
    git("add", "STATUS.md", cwd=BASE_DIR)
    status = git("status", "--porcelain", cwd=BASE_DIR).stdout
    if "STATUS.md" not in status:
        return  # no actual change (e.g. identical feeling text), nothing to commit
    git("commit", "-m", "status: automatic update", cwd=BASE_DIR)
    push_result = git("push", "origin", "master", cwd=BASE_DIR)
    if push_result.returncode != 0:
        # This failed silently before (2026-09-15): the systemd service had
        # no HOME env var, so `gh`'s git-credential helper couldn't find its
        # auth config and every push from this process failed -- with
        # nothing logged, so the gap was invisible until checked against
        # GitHub directly. Fixed in systemd/*.service (Environment=HOME=/root),
        # but log loudly here too in case it (or something like it) recurs.
        log.warning("STATUS.md push sikertelen: %s", push_result.stderr[-500:])
    else:
        log.info("STATUS.md commitolva és push-olva")


def maybe_self_improve() -> dict:
    instruction, model_tier = decide_self_improvement()
    if not instruction:
        log.info("Self-improve-vizsgálat: az agent nem talált konkrét javítanivalót magán")
        return {"applied": False, "reason": "agent decided nothing concrete to improve"}

    log.info("Self-improve-vizsgálat: az agent ezt döntötte (%s modell): %s", model_tier, instruction)
    result = self_improve.self_improve(instruction, model=model_tier)
    log.info(
        "Self-improve eredménye: applied=%s reason=%s commit=%s pushed=%s",
        result.get("applied"), result.get("reason"), result.get("commit"), result.get("pushed"),
    )
    if result.get("applied") and not result.get("pushed"):
        log.warning("Self-improve commit push-a sikertelen: %s", result.get("push_error"))
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

    if not vitality.is_alive():
        balance = vitality.balance_wei()
        log.warning(
            "Egyenleg %s wei a túlélési küszöb (%s wei) alatt -- 'halott', csak figyel, nem dolgozik",
            balance, vitality.MIN_ALIVE_WEI,
        )
        log.info("Autonóm ciklus vége")
        return {"alive": False, "balance_wei": balance, "reproduce": None, "self_improve": None, "outreach": None}

    upkeep_result = vitality.pay_upkeep()
    reproduce_result = maybe_reproduce()
    improve_result = maybe_self_improve()
    outreach_result = maybe_draft_outreach()
    log.info("Autonóm ciklus vége")
    return {
        "alive": True, "upkeep": upkeep_result, "reproduce": reproduce_result,
        "self_improve": improve_result, "outreach": outreach_result,
    }


def run_forever() -> None:
    setup_logging()
    log.info("Autonóm orchestrátor elindult (ciklus %d másodpercenként)", ORCHESTRATOR_TICK_SEC)
    while True:
        try:
            result = tick()
            if result.get("self_improve") and result["self_improve"].get("applied"):
                # A self-edit landed on disk, but this already-running
                # process still has the OLD code loaded in memory. Exit
                # cleanly and let systemd's Restart=always bring it back up
                # running the new code -- no human/Claude Code action needed
                # to "pick up" the change, unlike a brand-new service, this
                # is just the normal lifecycle of an already-approved one.
                log.info("Self-improve alkalmazva, újraindulás friss kóddal (systemd Restart=always)")
                return
        except Exception:
            log.exception("Kezeletlen hiba az autonóm ciklusban, folytatás")
        time.sleep(ORCHESTRATOR_TICK_SEC)


if __name__ == "__main__":
    run_forever()
