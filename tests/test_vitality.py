import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vitality  # noqa: E402


def fake_wallet():
    return {"address": "0x1111111111111111111111111111111111111111", "private_key": "0x" + "11" * 32}


def test_is_alive_true_above_threshold():
    with patch("vitality.balance_wei", return_value=vitality.MIN_ALIVE_WEI + 1):
        assert vitality.is_alive() is True


def test_is_alive_false_below_threshold():
    with patch("vitality.balance_wei", return_value=vitality.MIN_ALIVE_WEI - 1):
        assert vitality.is_alive() is False


def test_is_alive_exactly_at_threshold_counts_as_alive():
    with patch("vitality.balance_wei", return_value=vitality.MIN_ALIVE_WEI):
        assert vitality.is_alive() is True


def test_can_reproduce_true_above_threshold():
    with patch("vitality.balance_wei", return_value=vitality.REPRODUCE_ABOVE_WEI + 1):
        assert vitality.can_reproduce() is True


def test_can_reproduce_false_below_threshold():
    with patch("vitality.balance_wei", return_value=vitality.REPRODUCE_ABOVE_WEI - 1):
        assert vitality.can_reproduce() is False


def test_reproduce_threshold_is_meaningfully_above_min_alive():
    # Sanity guard on the constants themselves: reproduction must require
    # real earned surplus, not just barely-alive balance.
    assert vitality.REPRODUCE_ABOVE_WEI > vitality.MIN_ALIVE_WEI * 10


def test_upkeep_smaller_than_one_job_price():
    # Upkeep should be a fraction of what one paid job earns (0.0005 ETH),
    # so genuine income can outpace the burn rate.
    from web3 import Web3
    job_price_wei = Web3.to_wei(0.0005, "ether")
    assert vitality.UPKEEP_WEI < job_price_wei


def test_pay_upkeep_sends_to_burn_address():
    fake_receipt = MagicMock(status=1)
    with patch("vitality.load_or_create_wallet", return_value=fake_wallet()), \
         patch.object(vitality, "_web3", None), \
         patch("vitality.Web3") as MockWeb3Class:
        mock_w3 = MagicMock()
        MockWeb3Class.return_value = mock_w3
        MockWeb3Class.to_checksum_address = lambda a: a
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 1
        mock_w3.eth.send_raw_transaction.return_value = MagicMock(hex=lambda: "0xdeadbeef")
        mock_w3.eth.wait_for_transaction_receipt.return_value = fake_receipt

        with patch("vitality.Account") as MockAccount:
            mock_acct = MagicMock(address="0xSender")
            mock_acct.sign_transaction.return_value = MagicMock(raw_transaction=b"signed")
            MockAccount.from_key.return_value = mock_acct

            result = vitality.pay_upkeep()

    assert result["paid"] is True
    sent_tx = mock_acct.sign_transaction.call_args[0][0]
    assert sent_tx["to"] == vitality.BURN_ADDRESS
    assert sent_tx["value"] == vitality.UPKEEP_WEI


def test_pay_upkeep_handles_failure_gracefully():
    with patch("vitality.load_or_create_wallet", return_value=fake_wallet()), \
         patch.object(vitality, "_web3", None), \
         patch("vitality.Web3") as MockWeb3Class:
        mock_w3 = MagicMock()
        MockWeb3Class.return_value = mock_w3
        mock_w3.eth.get_transaction_count.side_effect = Exception("RPC down")

        result = vitality.pay_upkeep()

    assert result["paid"] is False
    assert "error" in result


def test_fund_offspring_sends_inheritance_amount():
    fake_receipt = MagicMock(status=1)
    with patch("vitality.load_or_create_wallet", return_value=fake_wallet()), \
         patch.object(vitality, "_web3", None), \
         patch("vitality.Web3") as MockWeb3Class:
        mock_w3 = MagicMock()
        MockWeb3Class.return_value = mock_w3
        MockWeb3Class.to_checksum_address = lambda a: a
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 1
        mock_w3.eth.send_raw_transaction.return_value = MagicMock(hex=lambda: "0xfeed")
        mock_w3.eth.wait_for_transaction_receipt.return_value = fake_receipt

        with patch("vitality.Account") as MockAccount:
            mock_acct = MagicMock(address="0xSender")
            mock_acct.sign_transaction.return_value = MagicMock(raw_transaction=b"signed")
            MockAccount.from_key.return_value = mock_acct

            result = vitality.fund_offspring("0xChild")

    assert result["funded"] is True
    assert result["amount_wei"] == vitality.INHERITANCE_WEI
    sent_tx = mock_acct.sign_transaction.call_args[0][0]
    assert sent_tx["to"] == "0xChild"
    assert sent_tx["value"] == vitality.INHERITANCE_WEI
