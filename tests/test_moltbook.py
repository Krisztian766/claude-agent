"""Tests for Moltbook agent registration and credential management."""
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


class TestClaimCredentials:
    """Test credential claiming on Moltbook platform."""

    @mock.patch('moltbook.requests.get')
    def test_claim_credentials_success(self, mock_get, temp_creds_file):
        """Test successful credential claim."""
        mock_response = mock.Mock()
        mock_response.json.return_value = {"status": "claimed"}
        mock_get.return_value = mock_response

        result = moltbook.claim_credentials()

        assert result is True
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "agents/status" in call_args[0][0]
        assert call_args[1]["headers"]["Authorization"] == "Bearer moltbook_sk_test123"

    @mock.patch('moltbook.requests.get')
    def test_claim_credentials_api_error(self, mock_get, temp_creds_file):
        """Test credential claim with API error."""
        mock_get.side_effect = requests.RequestException("API error")

        result = moltbook.claim_credentials()

        assert result is False

    def test_claim_credentials_no_api_key(self, missing_creds_file):
        """Test credential claim with no API key available."""
        result = moltbook.claim_credentials()

        assert result is False


class TestGetAgentStatus:
    """Test fetching agent status from Moltbook."""

    @mock.patch('moltbook.requests.get')
    def test_get_status_success(self, mock_get, temp_creds_file):
        """Test successful status fetch."""
        expected_status = {"agent_id": "test-agent-id-123", "alive": True}
        mock_response = mock.Mock()
        mock_response.json.return_value = expected_status
        mock_get.return_value = mock_response

        status = moltbook.get_agent_status()

        assert status == expected_status

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


class TestRegisterProfile:
    """Test agent profile registration on Moltbook."""

    @mock.patch('moltbook.requests.post')
    def test_register_profile_success(self, mock_post, temp_creds_file):
        """Test successful profile registration."""
        mock_response = mock.Mock()
        mock_response.json.return_value = {"registered": True}
        mock_post.return_value = mock_response

        result = moltbook.register_agent_profile()

        assert result is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "agents/profile" in call_args[0][0]
        assert call_args[1]["headers"]["Authorization"] == "Bearer moltbook_sk_test123"
        profile_data = call_args[1]["json"]
        assert profile_data["name"] == "test-agent"
        assert "capabilities" in profile_data

    @mock.patch('moltbook.requests.post')
    def test_register_profile_api_error(self, mock_post, temp_creds_file):
        """Test profile registration with API error."""
        mock_post.side_effect = requests.RequestException("API error")

        result = moltbook.register_agent_profile()

        assert result is False

    def test_register_profile_no_api_key(self, missing_creds_file):
        """Test profile registration with no API key."""
        result = moltbook.register_agent_profile()

        assert result is False


class TestEnsureDiscovered:
    """Test complete discovery flow."""

    @mock.patch('moltbook.register_agent_profile')
    @mock.patch('moltbook.claim_credentials')
    def test_ensure_discovered_success(self, mock_claim, mock_register):
        """Test successful complete discovery."""
        mock_claim.return_value = True
        mock_register.return_value = True

        result = moltbook.ensure_discovered()

        assert result is True
        mock_claim.assert_called_once()
        mock_register.assert_called_once()

    @mock.patch('moltbook.register_agent_profile')
    @mock.patch('moltbook.claim_credentials')
    def test_ensure_discovered_claim_fails(self, mock_claim, mock_register):
        """Test discovery when claim fails."""
        mock_claim.return_value = False

        result = moltbook.ensure_discovered()

        assert result is False
        mock_register.assert_not_called()

    @mock.patch('moltbook.register_agent_profile')
    @mock.patch('moltbook.claim_credentials')
    def test_ensure_discovered_register_fails(self, mock_claim, mock_register):
        """Test discovery when registration fails."""
        mock_claim.return_value = True
        mock_register.return_value = False

        result = moltbook.ensure_discovered()

        assert result is False
