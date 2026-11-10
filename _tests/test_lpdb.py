"""Tests for edh/lpdb.py - LPDB API key and client."""

# Third-party
import pytest

# Local
from edh import lpdb, paths
from edh.exceptions import ConfigurationError
from edh.lpdb import LPDB_APP_NAME, load_api_key, open_client

# === load_api_key ===


def test_load_api_key_from_env(monkeypatch):
    """Test that the environment variable wins."""
    monkeypatch.setenv('LPDB_API_KEY', 'env-key')
    assert load_api_key() == 'env-key'


def test_load_api_key_from_file(monkeypatch, tmp_path):
    """Test the fallback on the token file's `lpdb` field."""
    monkeypatch.delenv('LPDB_API_KEY', raising=False)
    tokens = tmp_path / 'tokens.json'
    tokens.write_text('{"lpdb": "file-key"}', encoding='utf-8')
    monkeypatch.setattr(paths, 'TOKENS_JSON', tokens)
    assert load_api_key() == 'file-key'


@pytest.mark.parametrize(
    ('content', 'message'), [(None, 'No LPDB API key'), ('{}', 'No LPDB API key'), ('[', 'Invalid')]
)
def test_load_api_key_errors(monkeypatch, tmp_path, content, message):
    """Test that a missing, keyless or malformed token file is a configuration error."""
    monkeypatch.delenv('LPDB_API_KEY', raising=False)
    tokens = tmp_path / 'tokens.json'
    if content is not None:
        tokens.write_text(content, encoding='utf-8')
    monkeypatch.setattr(paths, 'TOKENS_JSON', tokens)
    with pytest.raises(ConfigurationError, match=message):
        load_api_key()


# === open_client ===


def test_open_client_passes_key_and_retries(monkeypatch):
    """Test that the client is named after the app, keyed with `load_api_key()` and given the retry settings."""
    calls = []
    monkeypatch.setenv('LPDB_API_KEY', 'env-key')
    monkeypatch.setattr(lpdb, 'LiquipediaClient', lambda *args, **kwargs: calls.append((args, kwargs)) or 'client')
    assert open_client(max_retries=5, retry_backoff_factor=4.0) == 'client'
    assert calls == [((LPDB_APP_NAME,), {'api_key': 'env-key', 'max_retries': 5, 'retry_backoff_factor': 4.0})]
