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
import logging
from pathlib import Path

from eth_account import Account
from web3 import Web3

from wallet import load_or_create_wallet

BASE_DIR = Path(__file__).resolve().parent
SEPOLIA_RPC = "https://ethereum-sepolia-rpc.publicnode.com"
BURN_ADDRESS = "0x000000000000000000000000000000000000dEaD"

UPKEEP_WEI = Web3.to_wei(0.00005, "ether")   # cost of one tick (~48/day => ~0.0024 ETH/day burn)
MIN_ALIVE_WEI = Web3.to_wei(0.0005, "ether")  # below this: dead, one job's worth or less left
REPRODUCE_ABOVE_WEI = Web3.to_wei(0.06, "ether")  # meaningfully above typical starting balance -- real earned surplus, not just faucet funding
INHERITANCE_WEI = Web3.to_wei(0.01, "ether")   # given to a new offspring's own wallet

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


def is_alive(cwd=None) -> bool:
    return balance_wei(cwd) >= MIN_ALIVE_WEI


def can_reproduce(cwd=None) -> bool:
    return balance_wei(cwd) >= REPRODUCE_ABOVE_WEI


def _send(to_address: str, amount_wei: int) -> dict:
    wallet = load_or_create_wallet()
    acct = Account.from_key(wallet["private_key"])
    w3 = web3()
    nonce = w3.eth.get_transaction_count(acct.address)
    tx = {
        "to": Web3.to_checksum_address(to_address),
        "value": amount_wei,
        "nonce": nonce,
        "gas": 21000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 11155111,
    }
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return {"tx_hash": tx_hash.hex(), "status": receipt.status}


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
