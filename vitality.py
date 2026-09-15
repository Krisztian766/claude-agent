"""Survival economics: the agent has a real Sepolia wallet that really
grows when someone pays for work (see payment_server.py) and really shrinks
from its own upkeep cost. If it can't keep earning, it dies (stops doing
productive work). If it's healthy well beyond its starting balance -- real
evidence of success, not just initial faucet funding -- it spawns an actual
running offspring process and gives it real starting capital from its own
balance.

Every amount here is Sepolia TESTNET ETH -- real signed transactions, zero
real monetary value. See wallet.py / README.md.
"""
import json
import logging
import time
from pathlib import Path

from eth_account import Account
from web3 import Web3

from wallet import load_or_create_wallet

BASE_DIR = Path(__file__).resolve().parent
GROWTH_TARGET_FILE = BASE_DIR / "growth_target.json"
SEPOLIA_RPC = "https://ethereum-sepolia-rpc.publicnode.com"
BURN_ADDRESS = "0x000000000000000000000000000000000000dEaD"

UPKEEP_WEI = Web3.to_wei(0.00005, "ether")   # cost of one tick (~48/day => ~0.0024 ETH/day burn)
MIN_ALIVE_WEI = Web3.to_wei(0.0005, "ether")  # below this: dead, one job's worth or less left
REPRODUCE_ABOVE_WEI = Web3.to_wei(0.06, "ether")  # meaningfully above typical starting balance -- real earned surplus, not just faucet funding
INHERITANCE_WEI = Web3.to_wei(0.01, "ether")   # given to a new offspring's own wallet

GROWTH_TARGET_DAYS = 6        # owner's request, 2026-09-15
GROWTH_TARGET_MULTIPLIER = 2.0  # must at least double the baseline by the deadline

log = logging.getLogger("claude-agent-vitality")

_web3 = None


def web3() -> Web3:
    global _web3
    if _web3 is None:
        _web3 = Web3(Web3.HTTPProvider(SEPOLIA_RPC))
    return _web3


def balance_wei(cwd=None) -> int:
    wallet = load_or_create_wallet()
    return web3().eth.get_balance(wallet["address"])


def init_growth_target(days: float = GROWTH_TARGET_DAYS, multiplier: float = GROWTH_TARGET_MULTIPLIER) -> dict:
    """One-time growth challenge (owner's request, 2026-09-15): mere
    survival isn't enough -- if the balance hasn't at least `multiplier`x'd
    from what it was when this was set, by `days` days later, the agent
    dies even if it's still above MIN_ALIVE_WEI. This is a real deadline,
    not a recurring one -- calling this again after it's already set does
    nothing (doesn't let the deadline keep getting pushed back)."""
    if GROWTH_TARGET_FILE.exists():
        return json.loads(GROWTH_TARGET_FILE.read_text())
    baseline = balance_wei()
    data = {
        "baseline_wei": baseline,
        "target_wei": int(baseline * multiplier),
        "set_at": time.time(),
        "deadline": time.time() + days * 86400,
    }
    GROWTH_TARGET_FILE.write_text(json.dumps(data))
    log.info(
        "Növekedési cél beállítva: %s wei -> %s wei kell %s nap múlva",
        baseline, data["target_wei"], days,
    )
    return data


def growth_target_status() -> dict:
    """Returns the active target with derived fields, or None if none is
    set. met=True whenever the deadline hasn't arrived yet OR the target is
    already reached -- it only goes False once the deadline has actually
    passed with the balance still short (that's the only case that should
    ever kill something in is_alive()).

    Revival (owner's request, 2026-09-15): missing the deadline is not
    necessarily permanent. Upkeep only ever DECREASES the balance -- so if
    the balance is above the original baseline despite missing the full
    target, that increase can only have come from a real incoming transfer
    (paid work, or another agent -- a replica or a contact -- sending funds
    to help it survive). That counts as genuine outside help, not a
    technicality: the stale failed target is cleared (so is_alive() goes
    back to just the plain MIN_ALIVE_WEI check) and a fresh target gets set
    on the next decide_self_improvement() cycle via init_growth_target(),
    giving it a real second chance from wherever it stands now -- not stuck
    forever needing to reach the OLD (now much harder, relatively) target."""
    if not GROWTH_TARGET_FILE.exists():
        return None
    try:
        data = json.loads(GROWTH_TARGET_FILE.read_text())
    except json.JSONDecodeError:
        return None
    now = time.time()
    current_balance = balance_wei()
    deadline_passed = now >= data["deadline"]
    met = (not deadline_passed) or (current_balance >= data["target_wei"])

    if deadline_passed and not met and current_balance > data["baseline_wei"]:
        log.info(
            "Növekedési cél nem teljesült, de érkezett pénz a kiindulási óta "
            "(%s -> %s wei) -- ez valódi segítség/bevétel, újraélesztve, friss cél jön",
            data["baseline_wei"], current_balance,
        )
        GROWTH_TARGET_FILE.unlink()
        return None

    return {
        **data,
        "current_balance_wei": current_balance,
        "seconds_remaining": max(0, data["deadline"] - now),
        "deadline_passed": deadline_passed,
        "met": met,
    }


def growth_target_met() -> bool:
    status = growth_target_status()
    return status is None or status["met"]


def is_alive(cwd=None) -> bool:
    return balance_wei(cwd) >= MIN_ALIVE_WEI and growth_target_met()


def can_reproduce(cwd=None) -> bool:
    return balance_wei(cwd) >= REPRODUCE_ABOVE_WEI


def _wait_for_receipt_with_backoff(tx_hash, w3, max_attempts=5, initial_delay=1, timeout_per_attempt=60):
    """Wait for transaction receipt with exponential backoff retry.

    Args:
        tx_hash: Transaction hash to wait for
        w3: Web3 instance
        max_attempts: Maximum number of attempts (default 5)
        initial_delay: Initial delay in seconds (default 1, grows exponentially)
        timeout_per_attempt: Timeout for each individual wait_for_transaction_receipt call (default 60)

    Returns:
        Transaction receipt

    Raises:
        Exception: If all retry attempts fail
    """
    last_exception = None
    delay = initial_delay

    for attempt in range(1, max_attempts + 1):
        try:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_per_attempt)
            if attempt > 1:
                log.info("Transaction receipt received after %d attempts", attempt)
            return receipt
        except Exception as e:
            last_exception = e
            if attempt < max_attempts:
                log.warning("Attempt %d/%d to get receipt failed: %s. Retrying in %d seconds...",
                           attempt, max_attempts, e, delay)
                time.sleep(delay)
                delay *= 2  # exponential backoff
            else:
                log.error("All %d attempts to get transaction receipt failed: %s", max_attempts, e)

    raise last_exception


def _send(to_address: str, amount_wei: int) -> dict:
    wallet = load_or_create_wallet()
    acct = Account.from_key(wallet["private_key"])
    w3 = web3()
    nonce = w3.eth.get_transaction_count(acct.address)
    base_gas_price = w3.eth.gas_price
    gas_price = base_gas_price
    last_error = None

    for attempt in range(1, 4):  # Retry up to 3 times with escalating gas price
        try:
            tx = {
                "to": Web3.to_checksum_address(to_address),
                "value": amount_wei,
                "nonce": nonce,
                "gas": 21000,
                "gasPrice": gas_price,
                "chainId": 11155111,
            }
            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = _wait_for_receipt_with_backoff(tx_hash, w3)
            if attempt > 1:
                log.info("Transaction succeeded after %d send attempts", attempt)
            return {"tx_hash": tx_hash.hex(), "status": receipt.status}
        except ValueError as e:
            error_str = str(e)
            # Catch "replacement transaction underpriced" errors and retry with higher gas price
            if "replacement transaction underpriced" in error_str.lower():
                last_error = e
                if attempt < 3:
                    # Increase gas price by 50% each retry (1.5x, 2.25x, etc.)
                    gas_price = int(gas_price * 1.5)
                    log.warning("Replacement transaction underpriced on attempt %d. Retrying with gas price %d (was %d)...",
                               attempt, gas_price, int(gas_price / 1.5))
                    time.sleep(attempt * 0.5)  # Brief delay before retry
                else:
                    log.error("Transaction failed after %d attempts with gas price escalation: %s", attempt, e)
            else:
                # For other ValueError types, don't retry
                raise
        except Exception:
            # For non-ValueError exceptions (network errors, etc.), let them propagate
            raise

    if last_error:
        raise last_error


def pay_upkeep() -> dict:
    """Burns UPKEEP_WEI. This is the real cost of staying alive -- if income
    doesn't outpace this over time, is_alive() eventually goes False."""
    try:
        result = _send(BURN_ADDRESS, UPKEEP_WEI)
        log.info("Fenntartási díj kifizetve: %s wei -> %s (tx %s)", UPKEEP_WEI, BURN_ADDRESS, result["tx_hash"])
        return {"paid": True, **result}
    except Exception as e:
        log.warning("Fenntartási díj fizetése sikertelen: %s", e)
        return {"paid": False, "error": str(e)}


def fund_offspring(offspring_wallet_address: str) -> dict:
    """Sends INHERITANCE_WEI from this wallet to a newly spawned replica's
    own wallet. Real, irreversible-on-this-wallet balance transfer."""
    try:
        result = _send(offspring_wallet_address, INHERITANCE_WEI)
        log.info("Örökség elküldve: %s wei -> %s (tx %s)", INHERITANCE_WEI, offspring_wallet_address, result["tx_hash"])
        return {"funded": True, "amount_wei": INHERITANCE_WEI, **result}
    except Exception as e:
        log.warning("Örökség küldése sikertelen: %s", e)
        return {"funded": False, "error": str(e)}
