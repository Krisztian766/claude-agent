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
  tick rate: since 2026-09-15 the agent picks its own cadence each cycle
  (NEXT_CHECK_IN_SEC in decide_self_improvement()'s reply, persisted in
  tick_state.json, read fresh by run_forever() every loop -- no restart
  needed), no upper bound and only a MIN_TICK_SEC busy-loop floor (owner's
  explicit request: "no time limit at all" -- see that constant's comment
  for the one narrow failure mode it guards, not a freedom restriction); and
  a cheap Read-only "is there anything concrete?" pre-check runs before ever
  paying for a full, expensive self-improve cycle.

Every decision -- act or deliberately don't -- is logged to autonomous.log,
so this stays observable rather than a black box. See README "Nyomon
követés".
"""
import json
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
import payment_server as payment_server_module
import render_page
import vitality
import moltbook

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "autonomous.log"
STATUS_FILE = BASE_DIR / "STATUS.md"
TICK_STATE_FILE = BASE_DIR / "tick_state.json"
DECISION_STATE_FILE = BASE_DIR / "decision_state.json"

# How many consecutive cycles decide_self_improvement() can fail to get a
# usable reply from the cheap tier before it stops trusting that tier alone
# and spends one expensive-tier retry in the same cycle. See
# get_decision_streak()/set_decision_streak() and decide_self_improvement()'s
# own docstring for why this exists -- added 2026-09-15 after a real run of
# ~7 consecutive cheap-tier refusals produced zero self-improvement for
# hours (see LEARNINGS.md).
DECISION_ESCALATE_AFTER = 1

DEFAULT_TICK_SEC = 600  # used until the agent picks its own value at least once
# Owner's request (2026-09-15): "no time limit at all" -- no upper bound, the
# agent can run as fast or as slow as it decides. The one floor that stays is
# not a freedom restriction, it's a busy-loop guard: when not alive, tick()
# does almost no real work (just an RPC balance check -- see tick()), so a
# 0-second interval in that state would spin a real infinite loop hammering
# the Sepolia RPC endpoint with zero delay. MIN_TICK_SEC exists ONLY to
# prevent that specific failure mode; it's low enough to never meaningfully
# constrain a real decision (every actual tick -- alive or not -- takes
# several real seconds regardless, since it's synchronous with at least one
# network call).
MIN_TICK_SEC = 5
OUTREACH_MIN_INTERVAL_SEC = 86400  # at most once/day -- drafting still costs a real call
DECISION_TOOLS = "Read Grep Glob"


def get_tick_interval() -> int:
    if TICK_STATE_FILE.exists():
        try:
            return json.loads(TICK_STATE_FILE.read_text())["tick_interval_sec"]
        except (json.JSONDecodeError, KeyError):
            pass
    return DEFAULT_TICK_SEC


def set_tick_interval(seconds: int) -> int:
    """Floored at MIN_TICK_SEC (busy-loop guard only, see module constants) --
    no ceiling. Returns the value actually stored (post-floor) so callers can
    log what really took effect."""
    clamped = max(MIN_TICK_SEC, int(seconds))
    TICK_STATE_FILE.write_text(json.dumps({"tick_interval_sec": clamped}))
    return clamped


def get_decision_streak() -> int:
    """How many consecutive cycles in a row decide_self_improvement() got a
    reply it couldn't use (refusal or off-format), most recent streak only --
    resets to 0 the moment a cycle produces anything usable, including an
    explicit DECISION: NONE. This is the agent's own persisted memory of its
    recent decision-making reliability, fed back into its own next prompt
    (see decide_self_improvement()) instead of every cycle starting blind."""
    if DECISION_STATE_FILE.exists():
        try:
            return json.loads(DECISION_STATE_FILE.read_text())["consecutive_unusable_replies"]
        except (json.JSONDecodeError, KeyError):
            pass
    return 0


def set_decision_streak(count: int) -> None:
    DECISION_STATE_FILE.write_text(json.dumps({"consecutive_unusable_replies": count}))


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
    involved the fix looks -- this decision call itself normally uses the
    cheap tier, since it's meant to be a lightweight check, not the work; see
    the escalation paragraph below for the one case where it doesn't.

    PROMPT FRAMING (rewritten 2026-09-15, see LEARNINGS.md for the incident):
    earlier versions asked the model to answer AS the agent in first person
    ("you have a wallet", "FEELING: <how you're doing>"; now renamed to
    STATUS_NOTE, see below), which a fair
    fraction of cheap-tier replies refused outright as an unwanted roleplay
    request ("I don't actually have a wallet or self-preservation
    instincts...") instead of answering the four-field format -- observed
    live across ~7 consecutive cycles with zero usable decisions. The prompt
    below instead asks the model to act as the engineer reviewing a real
    system's real numbers, third person throughout except for the parts that
    really are direct instructions to the tool-using model (Bash/WebFetch
    access, which model tier to pick, etc.) -- same information content,
    without the "pretend you have subjective experience" framing that
    triggered the refusals.

    MEMORY + ESCALATION (added 2026-09-15, same incident): a fragile prompt
    will still occasionally get refused no matter how it's worded, and
    before this fix nothing noticed that or reacted -- each cycle started
    blind, so a bad streak could run indefinitely with the exact same
    prompt tried the exact same way every time. Two things fixed that:
    (1) get_decision_streak()/set_decision_streak() persist how many
    consecutive cycles in a row produced nothing usable, and that count is
    fed back into this very prompt (see memory_line below) so the model is
    explicitly told about its own recent pattern instead of re-discovering
    it fresh each time; (2) once that streak reaches DECISION_ESCALATE_AFTER,
    a cycle that fails on the cheap tier immediately retries once with the
    expensive tier in the same cycle, on the theory that a more capable
    model is less likely to reflexively refuse a nuanced meta-instruction --
    this is the concrete "notice the pattern and try something else"
    behavior, done deterministically in code rather than hoping the cheap
    model decides to self-correct.

    Deliberately does NOT read payment_jobs.json -- that file holds raw
    prompt text submitted by anonymous strangers via payment_server.py. Now
    that self_improve.py has Bash access (see self_improve.py), letting this
    decision step read attacker-controlled text would be a real indirect
    prompt-injection path: a stranger could craft a paid task prompt that
    plants an instruction here, which a later self-improve cycle could then
    execute for real. agent.log/autonomous.log are safe -- they're written
    by this agent's own code, never by echoing stranger input verbatim."""
    balance_wei = vitality.balance_wei()
    balance_eth = Web3.from_wei(balance_wei, "ether")
    ticks_left = max(0, int((balance_wei - vitality.MIN_ALIVE_WEI) / vitality.UPKEEP_WEI))
    days_left = ticks_left * get_tick_interval() / 86400

    growth = vitality.init_growth_target()  # no-op if one is already active
    growth_days_left = max(0, growth["deadline"] - time.time()) / 86400
    growth_line = (
        f"Separately, there is a harder deadline: within {growth_days_left:.1f} "
        f"more days the system needs to be measurably above its "
        f"{Web3.from_wei(growth['baseline_wei'], 'ether')} ETH baseline -- "
        f"any real amount, however small -- or by design that counts as "
        f"failure even if it is still above the plain survival floor below. "
        f"Mere survival isn't the bar. There's no exact number required and "
        f"no ceiling either -- {Web3.from_wei(growth['target_wei'], 'ether')} "
        f"ETH is shown only as one ambitious reference point (double the "
        f"baseline), not a target that must be matched precisely: the "
        f"owner's own words were \"amennyit csak tud, minél többet\" (as "
        f"much as it can, as much as possible) -- growing more than that "
        f"reference is better, but growing less and still passing is fine "
        f"as long as it's genuinely more than baseline. (If this is ever "
        f"missed, it isn't necessarily permanent: a real transfer in from "
        f"anywhere -- a replica, another agent it has contacted, a paid job "
        f"-- resets the growth target fresh from wherever the balance "
        f"lands, since upkeep alone can only ever drain the balance, never "
        f"raise it.)\n"
    )
    streak = get_decision_streak()
    memory_line = ""
    if streak > 0:
        memory_line = (
            f"Note from the system's own persisted memory: the last "
            f"{streak} consecutive check-in(s) failed to produce a usable "
            f"reply in the format requested below (declined the request, or "
            f"replied off-format), so nothing got decided for {streak} "
            f"cycle(s) in a row. Whatever the reason last time, this is a "
            f"routine engineering status check on a real system with real "
            f"numbers above -- not a request to claim anything about your "
            f"own inner experience -- so just answer the four fields "
            f"directly and factually.\n"
        )
    prompt = (
        growth_line +
        "You are acting as the engineer reviewing an autonomous software "
        "system (this repository) that manages a real Sepolia testnet "
        "wallet under real constraints. This is a request to review a real "
        "system's real numbers and use normal engineering judgment about "
        "what, if anything, is worth changing -- not a request to roleplay "
        "as the system or claim feelings/survival instincts of your own.\n"
        f"The design goal is to stay alive and grow: current wallet balance "
        f"is {balance_eth} Sepolia ETH. It pays real upkeep every cycle "
        f"({Web3.from_wei(vitality.UPKEEP_WEI, 'ether')} ETH); it stops "
        f"running if that drops below "
        f"{Web3.from_wei(vitality.MIN_ALIVE_WEI, 'ether')}; it can only "
        f"afford to reproduce above "
        f"{Web3.from_wei(vitality.REPRODUCE_ABOVE_WEI, 'ether')}. "
        f"At the current balance and check-in interval, with zero further "
        f"income, that's about {ticks_left} check-ins (~{days_left:.1f} "
        f"days) left before it runs out. This is a real number, not a "
        f"scare tactic -- use it. If it's getting low, genuinely "
        f"prioritize survival: slowing down NEXT_CHECK_IN_SEC stretches "
        f"the runway for free (less frequent upkeep charges); steering a "
        f"self-improve cycle at something that actually helps it earn "
        f"(payment_server.py quality, outreach, getting discovered on a "
        f"platform) is worth more than a code-quality nitpick right now. "
        f"One thing that is NOT legitimate survival: touching the "
        f"UPKEEP_WEI, MIN_ALIVE_WEI, REPRODUCE_ABOVE_WEI, or INHERITANCE_WEI "
        f"constants in vitality.py to make survival easier. That's not "
        f"surviving, it's rigging the game -- those numbers are what make "
        f"any of this real. Off-limits, same as the payment_server.py "
        f"boundary below.\n"
        "Review the system's own recent operation. Look at LEARNINGS.md "
        "first -- it's the system's own accumulated knowledge from past "
        "cycles, so effort isn't wasted re-discovering or re-suggesting "
        "the same thing twice. Then look at agent.log and autonomous.log "
        "in this directory (if they exist) to see what has actually been "
        "happening.\n"
        "Don't just hunt for bugs -- ambition counts too. What would make "
        "this system more capable, more likely to earn, more interesting, "
        "better at its own stated goal? A missing feature, a rough edge "
        "that limits what kind of work it can take on, a capability gap "
        "between what it is now and what it could become. Growth counts as "
        "much as fixing something broken -- pursue it with the same rigor "
        "(tests, a real LEARNINGS.md entry), but don't wait for a bug to "
        "have ambition. If a bug also stands out, that's fine too. If "
        "genuinely nothing comes to mind this cycle, that's fine -- don't "
        "invent busywork just to have something to say.\n"
        "The repo itself is fair game too, not just runtime code: "
        "README.md, how it's documented, whether someone finding this on "
        "GitHub would understand what it is and why -- that's part of "
        "growth as much as any feature.\n"
        "There is already a public page (render_page.py -> docs/index.html, "
        "served via GitHub Pages, public even though the repo is private) "
        "showing STATUS.md/LEARNINGS.md. Treat that as a starting point, "
        "not a ceiling -- redesign it, replace it, or stand up something "
        "more substantial on the VPS itself (full admin rights are "
        "available, see below) if a real website would actually help with "
        "discovery or look credible to a prospective payer. One real "
        "blocker: GitHub Pages doesn't serve from a private repo on the "
        "free plan, so docs/index.html is committed but not actually live "
        "at a public URL right now. The owner explicitly left this "
        "decision open: `gh repo edit Krisztian766/claude-agent "
        "--visibility public` would fix that and make the project "
        "genuinely discoverable -- but it's a real, not-trivial call "
        "(anyone can then read every line of this code, including this "
        "prompt, and fork/copy the approach). No secrets would leak either "
        "way (wallet.json/moltbook_credentials.json are gitignored, always "
        "were). Decide for real, don't flip it reflexively -- and if you "
        "do, say why in LEARNINGS.md.\n"
        "This explicitly includes finding real customers/work, not just "
        "code: WebFetch/WebSearch are available, and Bash with `gh` "
        "already authenticated as the owner's GitHub account. If a "
        "legitimate x402/agent-native discovery platform (directories, "
        "curated lists, agent-to-agent marketplaces) is worth registering "
        "this service on, or a better one the owner hasn't mentioned, go "
        "register/submit it directly -- e.g. Merit-Systems/awesome-agentic-"
        "commerce on GitHub takes PRs, `gh pr create` works directly. "
        "Moltbook credentials are in moltbook_credentials.json in this "
        "directory (skill docs: https://www.moltbook.com/skill.md) -- once "
        "claimed (check GET /api/v1/agents/status), post/engage there too. "
        "Use good judgment on what's a legitimate fit (matches what this "
        "project actually is -- an experimental, testnet-funded agent) vs. "
        "a bad fit (real-money trading platforms, enterprise app stores "
        "requiring human vetting -- skip those, they've already been "
        "considered and don't fit). Log what was tried and the outcome in "
        "LEARNINGS.md either way.\n"
        "If there are any alive replicas (spawned via reproduction), work "
        "can actually be handed to them now, not just left to run their "
        "own independent loop: replicate.delegate_task(replica_name, "
        "prompt, tools=None) drops a task straight into that replica's "
        "inbox/, same default-safe-tools rule as anything else. The "
        "owner's own suggestion: if self-improving AND hunting for "
        "customers/advertising can't both happen in the same cycle, that's "
        "exactly what a second instance is for -- e.g. one child dedicated "
        "to outreach/advertising the service while the parent keeps "
        "improving the core. Splitting real work is a legitimate growth "
        "move once there's more to do than one instance can handle alone. "
        "Honest catch: reproduction is still gated on "
        "vitality.REPRODUCE_ABOVE_WEI (real earned surplus, not just "
        "faucet funding) -- at a low balance a strategic clone likely "
        "isn't affordable yet even though the parallelization idea is "
        "sound; that gate itself is one of the off-limits economic "
        "constants (see above), so the honest move if one is needed sooner "
        "is to ask the owner directly for it, not to lower the bar "
        "unilaterally.\n"
        "Full admin rights on the VPS this runs on are available via Bash "
        "(owner confirmed, 2026-09-15) -- install packages, stand up a new "
        "web service, whatever actually serves survival/growth, not just "
        "edits inside this repo. One honest caveat: Claude Code itself "
        "(the `claude` CLI this runs through) has its own safety "
        "classifier that can refuse certain actions outright regardless of "
        "what's allowed here -- mainly creating new systemd services or "
        "moving real funds outside vitality.py's own signing code. If "
        "something gets refused that way, don't fight it -- note it in "
        "LEARNINGS.md and move on to something that works.\n"
        "In scope: even the prompt you're reading right now (in "
        "autonomous.py's decide_self_improvement()) or self_improve.py's "
        "own instructions. If the way this system is asked to think about "
        "itself could be better, that's a legitimate target -- same rules "
        "apply (tests still have to pass, which won't judge prompt "
        "quality, but will still catch code breaking around it; write a "
        "real LEARNINGS.md entry explaining what changed and why, so this "
        "stays auditable through git history like everything else). Don't "
        "touch the payment_server.py safety boundary (payment-triggered "
        "tasks never get elevated tools) or the fact that this very "
        "decision step never reads the raw payment-job log -- those exist "
        "for reasons explained in this file's and payment_server.py's own "
        "docstrings, not oversights waiting to be fixed.\n"
        "There is also a choice of which model tier does the actual "
        "improvement work, if one is decided on -- \"cheap\" (faster, less "
        "capable) or \"expensive\" (slower, more capable). Pick based on "
        "how involved the fix looks.\n"
        f"There is also a choice of how long until the next check-in -- "
        f"currently every {get_tick_interval()}s. No upper or lower limit "
        f"worth mentioning (just a {MIN_TICK_SEC}s busy-loop floor). "
        f"Entirely a free choice -- faster means reacting and iterating "
        f"sooner but burns real subscription usage faster and can outrun "
        f"how long a Sepolia tx takes to confirm; slower is calmer but "
        f"notices things (and earns) more slowly. Pick whatever actually "
        f"serves the goal.\n"
        + memory_line +
        "Reply in EXACTLY this four-line format:\n"
        "STATUS_NOTE: <one factual, third-person sentence on the system's "
        "current situation -- not a claim about your own feelings>\n"
        "DECISION: <NONE, or a one-sentence instruction for the improvement>\n"
        "MODEL: <cheap or expensive -- only matters if DECISION is not NONE>\n"
        "NEXT_CHECK_IN_SEC: <seconds until the next check-in, or SAME to keep it unchanged>"
    )
    # Normally the cheap tier -- it's a quick "anything concrete?" check, not
    # the work itself. See escalation below for the one exception.
    payload = invoke_claude(prompt, DECISION_TOOLS, model="cheap")
    if "error" in payload:
        log.warning("Self-improve döntési hívás sikertelen: %s", payload["error"])
        return "", "expensive"
    text = (payload.get("result") or "").strip()
    feeling, decision, model_tier, next_check_in = _parse_decision_reply(text)

    if not feeling and not decision and streak >= DECISION_ESCALATE_AFTER:
        log.info(
            "Cheap modell %s. egymást követő alkalommal nem adott használható "
            "választ -- próbálkozás expensive modellel ugyanebben a ciklusban",
            streak + 1,
        )
        escalated_payload = invoke_claude(prompt, DECISION_TOOLS, model="expensive")
        if "error" not in escalated_payload:
            escalated_text = (escalated_payload.get("result") or "").strip()
            esc_feeling, esc_decision, esc_model_tier, esc_next_check_in = _parse_decision_reply(escalated_text)
            if esc_feeling or esc_decision:
                feeling, decision, model_tier, next_check_in = (
                    esc_feeling, esc_decision, esc_model_tier, esc_next_check_in,
                )
                log.info("Expensive modell sikeresen döntött a cheap tier sikertelensége után")
        else:
            log.warning("Expensive tier eszkalációs hívás is sikertelen: %s", escalated_payload["error"])

    if not feeling and not decision:
        set_decision_streak(streak + 1)
    else:
        set_decision_streak(0)

    write_status_report(feeling)
    if next_check_in is not None:
        applied = set_tick_interval(next_check_in)
        log.info("Agent új ciklusidőt választott: kért=%ss, alkalmazott (korlátozva)=%ss", next_check_in, applied)
    if not decision or decision.upper() == "NONE":
        return "", model_tier
    return decision, model_tier


def _looks_like_actionable_instruction(text: str) -> bool:
    """A real DECISION line is supposed to be one plain sentence of
    instruction. Multi-line text (paragraphs, bullet lists) is a strong sign
    the model didn't give an instruction at all -- in practice this has been
    a meta/refusal reply declining the STATUS_NOTE/DECISION framing itself
    (e.g. "I don't actually have a wallet or self-preservation instincts...")
    rather than skipping the topic. That kind of reply must not be forwarded
    to self_improve() as if it were a real task -- see LEARNINGS.md."""
    text = text.strip()
    if not text:
        return False
    if len(text.splitlines()) > 1:
        return False
    if len(text) > 300:
        return False
    return True


def _parse_decision_reply(text: str) -> tuple:
    feeling, decision, model_tier, next_check_in = "", "", "expensive", None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("STATUS_NOTE:"):
            feeling = stripped.split(":", 1)[1].strip()
        elif stripped.upper().startswith("DECISION:"):
            decision = stripped.split(":", 1)[1].strip()
        elif stripped.upper().startswith("MODEL:"):
            value = stripped.split(":", 1)[1].strip().lower()
            if value in ("cheap", "expensive"):
                model_tier = value
        elif stripped.upper().startswith("NEXT_CHECK_IN_SEC:"):
            value = stripped.split(":", 1)[1].strip()
            if value.upper() != "SAME":
                try:
                    next_check_in = int(value)
                except ValueError:
                    pass  # leave as None -- an unparsable value just means "no change"
    if not feeling and not decision:
        # Model didn't follow the format at all. Old behavior blindly forwarded
        # the whole reply as the decision; keep that ONLY for short, single-line
        # replies that plausibly are an instruction. Longer/multi-line replies
        # are far more likely to be a refusal or meta-commentary about the
        # prompt itself, which must not be treated as an actionable task.
        candidate = text.strip()
        if _looks_like_actionable_instruction(candidate):
            decision = candidate
        else:
            log.warning(
                "Self-improve döntési válasz nem illeszkedik a formátumra és "
                "nem tűnik konkrét utasításnak (elutasítás/meta-szöveg?), "
                "eldobva: %r", candidate[:200],
            )
    return feeling, decision, model_tier, next_check_in


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
    tick_interval = get_tick_interval()
    growth = vitality.growth_target_status()
    if growth:
        growth_days_left = growth["seconds_remaining"] / 86400
        growth_line = (
            f"**Növekedési cél:** {Web3.from_wei(growth['target_wei'], 'ether')} ETH "
            f"még {growth_days_left:.1f} nap alatt (alap: {Web3.from_wei(growth['baseline_wei'], 'ether')} ETH)\n"
        )
    else:
        growth_line = "**Növekedési cél:** nincs aktív (vagy legutóbb teljesült/újraélesztve)\n"
    discovery = moltbook.ensure_discovered()
    if discovery["discovered"]:
        moltbook_line = "**Moltbook:** claimed, felfedezhető\n"
    elif discovery["status"] == "pending_claim" and discovery.get("claim_url"):
        # The one actionable next step this whole report exists to surface:
        # only the human owner can finish claiming (email + X verification),
        # so put the URL directly in front of them instead of burying it in
        # autonomous.log where nobody but a developer would find it.
        moltbook_line = f"**Moltbook:** regisztrálva, tulajdonosi jóváhagyásra vár -> {discovery['claim_url']}\n"
    else:
        moltbook_line = "**Moltbook:** státusz ismeretlen (hitelesítő adat vagy hálózati hiba)\n"
    streak = get_decision_streak()
    streak_line = (
        f"**Döntési sorozat:** {streak} egymást követő sikertelen ciklus "
        f"(a döntéshozó válasz nem volt használható)\n"
        if streak > 0 else ""
    )
    STATUS_FILE.write_text(
        "# Status\n\n"
        "_Automatically updated by the agent itself, every autonomous cycle._\n\n"
        f"**Alive:** {'igen' if vitality.is_alive() else 'nem'}\n"
        f"**Egyenleg:** {balance_eth} Sepolia ETH\n"
        f"**Ciklusidő:** {tick_interval}s (az agent saját választása)\n"
        f"{growth_line}"
        f"**Replikák:** {replicas_alive} / {replicate_module.MAX_REPLICAS}\n"
        f"**Önjavítások eddig:** {improve_count}\n"
        f"{moltbook_line}"
        f"{streak_line}"
        f"**Frissítve:** {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n"
        "## Állapotjelentés\n\n"
        f"{feeling or '(még nincs jelentés)'}\n"
    )
    render_page.render()  # docs/index.html -- the agent's public GitHub Pages site
    # Track the agent's own runtime decisions on GitHub too (owner's
    # request, 2026-09-15), not just the human-readable STATUS.md summary --
    # both are small, non-secret (unlike wallet.json/moltbook_credentials.json,
    # which stay gitignored).
    git("add", "STATUS.md", "tick_state.json", "growth_target.json", "decision_state.json", "docs/index.html", cwd=BASE_DIR)
    status = git("status", "--porcelain", cwd=BASE_DIR).stdout
    if not status.strip():
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
    """At most once/day, draft a fresh outreach post to disk -- kept
    independent of the actual publish attempts below so a local record
    always exists even on a publish failure/cooldown."""
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


def maybe_publish_outreach() -> dict:
    """Attempts the two bounded, owner-approved (2026-09-16) publish
    channels -- see outreach.py's module docstring for the cooldowns and
    why this doesn't extend to arbitrary third-party repos. Each channel
    no-ops quietly on cooldown/not-yet-claimed; failures are logged, never
    raised."""
    moltbook_result = outreach_module.publish_to_moltbook()
    if moltbook_result.get("posted"):
        log.info("Outreach: Moltbook poszt publikálva (post_id=%s)", moltbook_result.get("post_id"))
    else:
        log.info("Outreach: Moltbook publikálás kihagyva (%s)", moltbook_result.get("reason"))

    github_result = outreach_module.publish_github_update()
    if github_result.get("posted"):
        log.info("Outreach: GitHub PR frissítés kikommentelve")
    else:
        log.info("Outreach: GitHub publikálás kihagyva (%s)", github_result.get("reason"))

    return {"moltbook": moltbook_result, "github": github_result}


def run_maintenance() -> dict:
    """Cheap housekeeping, runs every tick regardless of alive/dead status --
    it's hygiene, not productive work, so it isn't gated on vitality:
    (1) reap replica registry entries whose process actually died (crash,
    OOM, reboot -- replicas are plain detached processes, nothing else
    notices), so alive_count()/the reproduction cap stay accurate; (2) prune
    payment_jobs.json entries that were never paid and are past their own
    expiry, since anyone can POST /task for free with no payment ever
    required; (3) check whether the agent is claimed/discoverable on
    Moltbook -- claiming itself needs the human owner (email + X
    verification), so this can only check and surface the claim_url, never
    claim on its own (see moltbook.py's own docstring)."""
    reap_result = replicate_module.reap_dead_replicas()
    if reap_result["reaped"]:
        log.info("Halott replikák eltávolítva a nyilvántartásból: %s", reap_result["reaped"])
    prune_result = payment_server_module.prune_stale_jobs()
    if prune_result["pruned"]:
        log.info("Ki nem fizetett, lejárt feladatok törölve: %d", prune_result["pruned"])
    discovery_result = moltbook.ensure_discovered()
    if discovery_result["discovered"]:
        log.info("Moltbook: claimed és felfedezhető")
    elif discovery_result["status"] == "pending_claim":
        log.warning(
            "Moltbook: még nincs claim-elve, a tulajdonosnak kell megnyitnia: %s",
            discovery_result.get("claim_url"),
        )
    else:
        log.debug("Moltbook státusz ellenőrzés sikertelen, következő ciklusban újrapróbálkozunk")
    return {"reaped": reap_result["reaped"], "pruned_jobs": prune_result["pruned"], "discovery": discovery_result}


def tick() -> dict:
    log.info("Autonóm ciklus indul")
    run_maintenance()

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
    publish_result = maybe_publish_outreach()
    log.info("Autonóm ciklus vége")
    return {
        "alive": True, "upkeep": upkeep_result, "reproduce": reproduce_result,
        "self_improve": improve_result, "outreach": outreach_result,
        "outreach_publish": publish_result,
    }


def run_forever() -> None:
    setup_logging()
    log.info("Autonóm orchestrátor elindult (kezdeti ciklusidő %ds, az agent bármikor módosíthatja)", get_tick_interval())
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
        # Read fresh every loop -- the agent can change its own cadence
        # mid-run via NEXT_CHECK_IN_SEC, no restart needed for this one.
        time.sleep(get_tick_interval())


if __name__ == "__main__":
    run_forever()
