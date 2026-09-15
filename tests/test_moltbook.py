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
