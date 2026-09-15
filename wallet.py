"""Sepolia testnet wallet for the payment server. Real signed keypair, zero
real financial value -- same pattern as the earlier automaton-agent project.
"""
import json
from pathlib import Path

from eth_account import Account

WALLET_PATH = Path(__file__).resolve().parent / "wallet.json"


def load_or_create_wallet() -> dict:
    if WALLET_PATH.exists():
        return json.loads(WALLET_PATH.read_text())
    acct = Account.create()
    wallet = {"address": acct.address, "private_key": acct.key.hex()}
    WALLET_PATH.write_text(json.dumps(wallet, indent=2))
    WALLET_PATH.chmod(0o600)
    return wallet
