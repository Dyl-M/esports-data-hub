"""Tests for edh/predictions/run.py - history command line (fake client, no live calls)."""

# Standard library
import logging
import runpy
import sys

# Third-party
import pytest
from liquipydia import RateLimitError

# Local
from edh import paths
from edh.predictions import run
from edh.predictions.constants import HISTORY_MAX_RETRIES, HISTORY_RETRY_BACKOFF
from edh.predictions.history import update_history

from .conftest import TODAY, FakeClient, make_record


class _Opened(FakeClient):
    """A fake client usable as a context manager, as `open_client` returns one."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None


def _fake_open_client(monkeypatch, records=(), error=None):
    """Make the command open a fake client; return the retry settings it was opened with."""
    opened = []

    def open_client(**retries):
        """Stand in for `edh.lpdb.open_client`, recording the retry settings."""
        opened.append(retries)
        return _Opened(records, error)

    monkeypatch.setattr(run, 'open_client', open_client)
    return opened


def test_main_updates_then_summarizes(monkeypatch, tmp_path, caplog):
    """Test a run: patient retries, the fetched matches cached, then a summary per year."""
    monkeypatch.setattr(paths, 'LPDB_CACHE_DIR', tmp_path)
    opened = _fake_open_client(monkeypatch, [make_record('m1', '2025-05-20'), make_record('m2', '2026-05-20')])
    with caplog.at_level(logging.INFO):
        assert run.main([]) == 0
    assert opened == [{'max_retries': HISTORY_MAX_RETRIES, 'retry_backoff_factor': HISTORY_RETRY_BACKOFF}]
    assert '2 finished team matches, 2 teams (2025: 1, 2026: 1)' in caplog.text


def test_main_offline_reads_the_cache_only(monkeypatch, tmp_path, caplog):
    """Test that `--offline` opens no client and summarizes what is cached."""
    monkeypatch.setattr(paths, 'LPDB_CACHE_DIR', tmp_path)
    update_history(FakeClient([make_record('m1', '2026-05-20')]), today=TODAY, cache_dir=tmp_path / 'rocketleague')
    opened = _fake_open_client(monkeypatch)
    with caplog.at_level(logging.INFO):
        assert run.main(['--offline']) == 0
    assert opened == []
    assert '1 finished team matches' in caplog.text


def test_module_entry_point(monkeypatch, tmp_path):
    """Test that `python -m edh.predictions.run` exits with main's code (0 for an empty offline summary)."""
    monkeypatch.setattr(paths, 'LPDB_CACHE_DIR', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['run', '--offline'])
    monkeypatch.delitem(sys.modules, 'edh.predictions.run')  # run a fresh copy as __main__, not the imported one
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module('edh.predictions.run', run_name='__main__')
    assert exit_info.value.code == 0


def test_main_reports_errors(monkeypatch, tmp_path, caplog):
    """Test that a failed fetch is logged and exits with 1."""
    monkeypatch.setattr(paths, 'LPDB_CACHE_DIR', tmp_path)
    _fake_open_client(monkeypatch, error=RateLimitError('Rate limit exceeded'))
    assert run.main(['--refetch']) == 1
    assert 'run again to resume' in caplog.text
