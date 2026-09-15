import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dark_research  # noqa: E402


def test_fetch_onion_rejects_non_http_urls():
    result = dark_research.fetch_onion("ftp://example.onion/file")
    assert result["ok"] is False
    assert "http" in result["error"]


def test_fetch_onion_uses_tor_socks_proxy():
    fake_resp = MagicMock(status_code=200, headers={"Content-Type": "text/html"})
    fake_resp.raw.read.return_value = b"<html>hello</html>"
    with patch("dark_research.requests.get", return_value=fake_resp) as get_mock:
        result = dark_research.fetch_onion("http://example.onion")

    assert result["ok"] is True
    assert result["text"] == "<html>hello</html>"
    called_kwargs = get_mock.call_args.kwargs
    assert called_kwargs["proxies"]["http"] == dark_research.TOR_PROXY
    assert called_kwargs["proxies"]["https"] == dark_research.TOR_PROXY


def test_fetch_onion_is_get_only_no_other_method_exists():
    # Enforced by having no post()/put()/delete() function in this module at
    # all -- not just by convention. This test fails loudly if one is ever
    # added, which should never happen without revisiting the module's
    # documented scope boundary first.
    assert not hasattr(dark_research, "post_onion")
    assert not hasattr(dark_research, "submit_onion")
    assert not hasattr(dark_research, "transact")


def test_fetch_onion_truncates_oversized_response():
    fake_resp = MagicMock(status_code=200, headers={"Content-Type": "text/plain"})
    fake_resp.raw.read.return_value = b"x" * (dark_research.MAX_RESPONSE_BYTES + 500)
    with patch("dark_research.requests.get", return_value=fake_resp):
        result = dark_research.fetch_onion("http://example.onion")

    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["text"]) == dark_research.MAX_RESPONSE_BYTES


def test_fetch_onion_handles_network_failure():
    with patch("dark_research.requests.get", side_effect=Exception("connection refused")):
        result = dark_research.fetch_onion("http://example.onion")

    assert result["ok"] is False
    assert "connection refused" in result["error"]


def test_fetch_onion_logs_every_attempt(tmp_path, monkeypatch):
    log_calls = []
    monkeypatch.setattr(dark_research.log, "info", lambda *a, **k: log_calls.append(("info", a)))
    monkeypatch.setattr(dark_research.log, "warning", lambda *a, **k: log_calls.append(("warning", a)))

    fake_resp = MagicMock(status_code=200, headers={"Content-Type": "text/html"})
    fake_resp.raw.read.return_value = b"ok"
    with patch("dark_research.requests.get", return_value=fake_resp):
        dark_research.fetch_onion("http://example.onion")
    assert any(c[0] == "info" for c in log_calls)

    log_calls.clear()
    with patch("dark_research.requests.get", side_effect=Exception("down")):
        dark_research.fetch_onion("http://example.onion")
    assert any(c[0] == "warning" for c in log_calls)
