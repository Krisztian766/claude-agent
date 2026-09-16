"""Tests for Moltbook agent status checking and discovery-gap surfacing.

moltbook.py deliberately cannot "claim" or "register a profile" itself --
per the real API (https://www.moltbook.com/skill.md), claiming requires the
human owner to complete email + X verification via a one-time claim_url, and
there is no agents/profile endpoint at all. These tests pin that: status
checking works, and ensure_discovered() surfaces the claim_url instead of
pretending success.
"""
import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest
import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import moltbook


@pytest.fixture
def temp_creds_file(monkeypatch):
    """Temporarily replace moltbook_credentials.json path for testing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_path = Path(f.name)
        test_creds = {
            "api_key": "moltbook_sk_test123",
            "agent_name": "test-agent",
            "agent_id": "test-agent-id-123",
            "profile_url": "https://www.moltbook.com/u/test-agent"
        }
        json.dump(test_creds, f)

    monkeypatch.setattr(moltbook, "CREDENTIALS_FILE", temp_path)
    yield temp_path
    temp_path.unlink()


@pytest.fixture
def missing_creds_file(monkeypatch):
    """Temporarily set credentials path to non-existent file."""
    temp_path = Path("/tmp/nonexistent_moltbook_creds_12345.json")
    monkeypatch.setattr(moltbook, "CREDENTIALS_FILE", temp_path)
    yield temp_path


class TestLoadCredentials:
    """Test credential loading from file."""

    def test_load_valid_credentials(self, temp_creds_file):
        """Test loading valid credentials from file."""
        creds = moltbook.load_credentials()
        assert creds["api_key"] == "moltbook_sk_test123"
        assert creds["agent_name"] == "test-agent"
        assert creds["agent_id"] == "test-agent-id-123"

    def test_load_missing_credentials_file(self, missing_creds_file):
        """Test handling of missing credentials file."""
        creds = moltbook.load_credentials()
        assert creds == {}

    def test_load_invalid_json(self, monkeypatch):
        """Test handling of malformed credentials file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = Path(f.name)
            f.write("{ invalid json }")

        monkeypatch.setattr(moltbook, "CREDENTIALS_FILE", temp_path)
        try:
            creds = moltbook.load_credentials()
            assert creds == {}
        finally:
            temp_path.unlink()


class TestGetAgentStatus:
    """Test fetching agent status from Moltbook."""

    @mock.patch('moltbook.requests.get')
    def test_get_status_success(self, mock_get, temp_creds_file):
        """Test successful status fetch."""
        expected_status = {"status": "claimed", "agent": {"id": "test-agent-id-123"}}
        mock_response = mock.Mock()
        mock_response.json.return_value = expected_status
        mock_get.return_value = mock_response

        status = moltbook.get_agent_status()

        assert status == expected_status
        call_args = mock_get.call_args
        assert "agents/status" in call_args[0][0]
        assert call_args[1]["headers"]["Authorization"] == "Bearer moltbook_sk_test123"

    @mock.patch('moltbook.requests.get')
    def test_get_status_api_error(self, mock_get, temp_creds_file):
        """Test status fetch with API error."""
        mock_get.side_effect = requests.RequestException("Network error")

        status = moltbook.get_agent_status()

        assert status == {}

    def test_get_status_no_credentials(self, missing_creds_file):
        """Test status fetch with no credentials."""
        status = moltbook.get_agent_status()

        assert status == {}


class TestEnsureDiscovered:
    """Test the discovery-status check (never claims/registers on its own --
    that needs the human owner, see moltbook.py's module docstring)."""

    @mock.patch('moltbook.get_agent_status')
    def test_claimed_agent_is_discovered(self, mock_status):
        mock_status.return_value = {"status": "claimed"}

        result = moltbook.ensure_discovered()

        assert result == {"discovered": True, "status": "claimed"}

    @mock.patch('moltbook.get_agent_status')
    def test_pending_claim_surfaces_claim_url(self, mock_status):
        mock_status.return_value = {
            "status": "pending_claim",
            "claim_url": "https://www.moltbook.com/claim/abc123",
        }

        result = moltbook.ensure_discovered()

        assert result == {
            "discovered": False,
            "status": "pending_claim",
            "claim_url": "https://www.moltbook.com/claim/abc123",
        }

    @mock.patch('moltbook.get_agent_status')
    def test_failed_status_check_reports_unknown(self, mock_status):
        mock_status.return_value = {}

        result = moltbook.ensure_discovered()

        assert result == {"discovered": False, "status": "unknown"}


class TestCreatePost:
    @mock.patch('moltbook.requests.post')
    def test_posts_without_verification_challenge(self, mock_post, temp_creds_file):
        mock_response = mock.Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "post123", "verification_required": False}
        mock_post.return_value = mock_response

        result = moltbook.create_post("Title", "Body text")

        assert result == {"posted": True, "post_id": "post123", "raw": mock_response.json.return_value}
        call_args = mock_post.call_args
        assert call_args[0][0].endswith("/posts")
        assert call_args[1]["json"] == {
            "submolt_name": "general", "title": "Title", "content": "Body text", "type": "text",
        }
        assert call_args[1]["headers"]["Authorization"] == "Bearer moltbook_sk_test123"

    def test_no_credentials_short_circuits(self, missing_creds_file):
        result = moltbook.create_post("Title", "Body")
        assert result == {"posted": False, "reason": "no credentials"}

    @mock.patch('moltbook.requests.post')
    def test_http_error_reported(self, mock_post, temp_creds_file):
        mock_response = mock.Mock()
        mock_response.status_code = 429
        mock_response.text = "rate limited"
        mock_post.return_value = mock_response

        result = moltbook.create_post("Title", "Body")

        assert result["posted"] is False
        assert "429" in result["reason"]

    @mock.patch('moltbook.requests.post')
    @mock.patch('moltbook._submit_verification', return_value=True)
    @mock.patch('moltbook._solve_challenge', return_value="7.00")
    def test_verification_challenge_solved_and_post_succeeds(self, mock_solve, mock_submit, mock_post, temp_creds_file):
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "post456",
            "verification_required": True,
            "verification": {"verification_code": "vc1", "challenge_text": "what is 3+4?"},
        }
        mock_post.return_value = mock_response

        result = moltbook.create_post("Title", "Body")

        assert result["posted"] is True
        mock_solve.assert_called_once_with("what is 3+4?")
        mock_submit.assert_called_once_with("moltbook_sk_test123", "vc1", "7.00")

    @mock.patch('moltbook.requests.post')
    @mock.patch('moltbook._submit_verification', return_value=False)
    @mock.patch('moltbook._solve_challenge', return_value="7.00")
    def test_verification_rejected_reports_failure(self, mock_solve, mock_submit, mock_post, temp_creds_file):
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "verification_required": True,
            "verification": {"verification_code": "vc1", "challenge_text": "what is 3+4?"},
        }
        mock_post.return_value = mock_response

        result = moltbook.create_post("Title", "Body")

        assert result == {"posted": False, "reason": "verification answer rejected"}

    @mock.patch('moltbook.requests.post')
    def test_network_error_reported(self, mock_post, temp_creds_file):
        mock_post.side_effect = requests.RequestException("timeout")

        result = moltbook.create_post("Title", "Body")

        assert result == {"posted": False, "reason": "timeout"}


class TestSolveChallenge:
    @mock.patch('moltbook.invoke_claude')
    def test_returns_stripped_result(self, mock_invoke):
        mock_invoke.return_value = {"result": "  7.00  "}
        assert moltbook._solve_challenge("3+4") == "7.00"

    @mock.patch('moltbook.invoke_claude')
    def test_returns_empty_on_error(self, mock_invoke):
        mock_invoke.return_value = {"error": "boom"}
        assert moltbook._solve_challenge("3+4") == ""
