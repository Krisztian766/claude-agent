import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, call

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


def test_wait_for_receipt_with_backoff_succeeds_immediately():
    """Test that backoff returns receipt immediately on first success."""
    fake_receipt = MagicMock(status=1)
    mock_w3 = MagicMock()
    mock_w3.eth.wait_for_transaction_receipt.return_value = fake_receipt

    result = vitality._wait_for_receipt_with_backoff("0xhash", mock_w3, max_attempts=3)

    assert result == fake_receipt
    mock_w3.eth.wait_for_transaction_receipt.assert_called_once()


def test_wait_for_receipt_with_backoff_retries_on_timeout():
    """Test that backoff retries when wait_for_transaction_receipt times out."""
    fake_receipt = MagicMock(status=1)
    mock_w3 = MagicMock()

    # First attempt fails with timeout, second succeeds
    mock_w3.eth.wait_for_transaction_receipt.side_effect = [
        TimeoutError("Transaction receipt not available"),
        fake_receipt,
    ]

    with patch("vitality.time.sleep") as mock_sleep:
        result = vitality._wait_for_receipt_with_backoff("0xhash", mock_w3, max_attempts=3, initial_delay=1)

    assert result == fake_receipt
    assert mock_w3.eth.wait_for_transaction_receipt.call_count == 2
    # Verify exponential backoff: first delay is 1 second
    mock_sleep.assert_called_once_with(1)


def test_wait_for_receipt_with_backoff_exponential_delay():
    """Test that delays grow exponentially: 1s, 2s, 4s, etc."""
    fake_receipt = MagicMock(status=1)
    mock_w3 = MagicMock()

    # Fail 3 times, succeed on 4th attempt
    mock_w3.eth.wait_for_transaction_receipt.side_effect = [
        TimeoutError("Attempt 1"),
        TimeoutError("Attempt 2"),
        TimeoutError("Attempt 3"),
        fake_receipt,
    ]

    with patch("vitality.time.sleep") as mock_sleep:
        result = vitality._wait_for_receipt_with_backoff("0xhash", mock_w3, max_attempts=5, initial_delay=1)

    assert result == fake_receipt
    # Verify exponential backoff delays: 1, 2, 4
    assert mock_sleep.call_count == 3
    mock_sleep.assert_has_calls([call(1), call(2), call(4)])


def test_wait_for_receipt_with_backoff_gives_up_after_max_attempts():
    """Test that backoff raises exception after exhausting max attempts."""
    mock_w3 = MagicMock()
    mock_w3.eth.wait_for_transaction_receipt.side_effect = TimeoutError("Network is congested")

    with patch("vitality.time.sleep"):
        try:
            vitality._wait_for_receipt_with_backoff("0xhash", mock_w3, max_attempts=2, initial_delay=1)
            assert False, "Expected TimeoutError to be raised"
        except TimeoutError as e:
            assert "Network is congested" in str(e)

    # Verify it made exactly max_attempts calls
    assert mock_w3.eth.wait_for_transaction_receipt.call_count == 2


def test_send_uses_backoff_for_receipt():
    """Test that _send uses exponential backoff for wait_for_transaction_receipt."""
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

        # Simulate temporary network congestion: timeout once, then succeed
        mock_w3.eth.wait_for_transaction_receipt.side_effect = [
            TimeoutError("Temporary congestion"),
            fake_receipt,
        ]

        with patch("vitality.Account") as MockAccount, \
             patch("vitality.time.sleep"):
            mock_acct = MagicMock(address="0xSender")
            mock_acct.sign_transaction.return_value = MagicMock(raw_transaction=b"signed")
            MockAccount.from_key.return_value = mock_acct

            result = vitality.pay_upkeep()

    # Should succeed despite the first timeout
    assert result["paid"] is True
    # Should have retried (2 calls to wait_for_transaction_receipt)
    assert mock_w3.eth.wait_for_transaction_receipt.call_count == 2


def test_send_retries_on_replacement_transaction_underpriced():
    """Test that _send escalates gas price when replacement transaction underpriced."""
    fake_receipt = MagicMock(status=1)
    with patch("vitality.load_or_create_wallet", return_value=fake_wallet()), \
         patch.object(vitality, "_web3", None), \
         patch("vitality.Web3") as MockWeb3Class:
        mock_w3 = MagicMock()
        MockWeb3Class.return_value = mock_w3
        MockWeb3Class.to_checksum_address = lambda a: a
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 100  # Base gas price

        # First send fails with underpriced error, second succeeds
        mock_w3.eth.send_raw_transaction.side_effect = [
            ValueError("replacement transaction underpriced"),
            MagicMock(hex=lambda: "0xdeadbeef"),
        ]
        mock_w3.eth.wait_for_transaction_receipt.return_value = fake_receipt

        with patch("vitality.Account") as MockAccount, \
             patch("vitality.time.sleep"):
            mock_acct = MagicMock(address="0xSender")
            mock_acct.sign_transaction.return_value = MagicMock(raw_transaction=b"signed")
            MockAccount.from_key.return_value = mock_acct

            result = vitality.pay_upkeep()

    # Should succeed after escalation
    assert result["paid"] is True

    # Should have tried to send twice
    assert mock_w3.eth.send_raw_transaction.call_count == 2

    # Verify gas price was escalated in second attempt
    # First call: gas_price = 100
    # Second call: gas_price = int(100 * 1.5) = 150
    first_tx = mock_acct.sign_transaction.call_args_list[0][0][0]
    second_tx = mock_acct.sign_transaction.call_args_list[1][0][0]
    assert first_tx["gasPrice"] == 100
    assert second_tx["gasPrice"] == 150  # 1.5x escalation


def test_send_escalates_gas_multiple_times():
    """Test that gas price escalates exponentially on repeated underpriced errors."""
    fake_receipt = MagicMock(status=1)
    with patch("vitality.load_or_create_wallet", return_value=fake_wallet()), \
         patch.object(vitality, "_web3", None), \
         patch("vitality.Web3") as MockWeb3Class:
        mock_w3 = MagicMock()
        MockWeb3Class.return_value = mock_w3
        MockWeb3Class.to_checksum_address = lambda a: a
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 100

        # Fail twice, succeed on third attempt
        mock_w3.eth.send_raw_transaction.side_effect = [
            ValueError("replacement transaction underpriced"),
            ValueError("replacement transaction underpriced"),
            MagicMock(hex=lambda: "0xdeadbeef"),
        ]
        mock_w3.eth.wait_for_transaction_receipt.return_value = fake_receipt

        with patch("vitality.Account") as MockAccount, \
             patch("vitality.time.sleep"):
            mock_acct = MagicMock(address="0xSender")
            mock_acct.sign_transaction.return_value = MagicMock(raw_transaction=b"signed")
            MockAccount.from_key.return_value = mock_acct

            result = vitality.fund_offspring("0xChild")

    # Should eventually succeed
    assert result["funded"] is True

    # Should have tried three times
    assert mock_w3.eth.send_raw_transaction.call_count == 3

    # Verify escalation: 100 -> 150 -> 225
    gas_prices = [mock_acct.sign_transaction.call_args_list[i][0][0]["gasPrice"] for i in range(3)]
    assert gas_prices == [100, 150, 225]


def test_send_gives_up_after_max_gas_price_escalations():
    """Test that _send gives up after 3 attempts with gas price escalation."""
    with patch("vitality.load_or_create_wallet", return_value=fake_wallet()), \
         patch.object(vitality, "_web3", None), \
         patch("vitality.Web3") as MockWeb3Class:
        mock_w3 = MagicMock()
        MockWeb3Class.return_value = mock_w3
        MockWeb3Class.to_checksum_address = lambda a: a
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 100

        # All attempts fail
        mock_w3.eth.send_raw_transaction.side_effect = ValueError("replacement transaction underpriced")

        with patch("vitality.Account") as MockAccount, \
             patch("vitality.time.sleep"):
            mock_acct = MagicMock(address="0xSender")
            mock_acct.sign_transaction.return_value = MagicMock(raw_transaction=b"signed")
            MockAccount.from_key.return_value = mock_acct

            result = vitality.pay_upkeep()

    # Should have failed
    assert result["paid"] is False
    assert "replacement transaction underpriced" in result["error"]

    # Should have tried exactly 3 times (3 send attempts with gas price escalation)
    assert mock_w3.eth.send_raw_transaction.call_count == 3


def test_send_does_not_retry_other_value_errors():
    """Test that _send does not retry on ValueError that isn't about replacement pricing."""
    with patch("vitality.load_or_create_wallet", return_value=fake_wallet()), \
         patch.object(vitality, "_web3", None), \
         patch("vitality.Web3") as MockWeb3Class:
        mock_w3 = MagicMock()
        MockWeb3Class.return_value = mock_w3
        MockWeb3Class.to_checksum_address = lambda a: a
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 100

        # Error that's NOT about replacement pricing
        mock_w3.eth.send_raw_transaction.side_effect = ValueError("invalid transaction")

        with patch("vitality.Account") as MockAccount:
            mock_acct = MagicMock(address="0xSender")
            mock_acct.sign_transaction.return_value = MagicMock(raw_transaction=b"signed")
            MockAccount.from_key.return_value = mock_acct

            result = vitality.pay_upkeep()

    # Should have failed
    assert result["paid"] is False
    assert "invalid transaction" in result["error"]

    # Should have tried only once (no retry for non-underpriced errors)
    assert mock_w3.eth.send_raw_transaction.call_count == 1
