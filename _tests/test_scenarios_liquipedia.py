"""Tests for edh/scenarios/sources/liquipedia.py - LPDB matches to series (recorded payloads, no live calls)."""

# Standard library
import json
import logging
from types import SimpleNamespace

import pytest

# Third-party
from liquipydia import ApiResponse, LiquipediaError, Match

# Local
from edh import paths
from edh.exceptions import ConfigurationError, DataSourceError
from edh.scenarios.config import load_league
from edh.scenarios.sources.liquipedia import LiquipediaSource, load_api_key, to_series

from .conftest import FIXTURES_DIR, make_league

LPDB_DIR = FIXTURES_DIR / 'lpdb'


def _load(name):
    """Load a recorded LPDB payload."""
    return json.loads((LPDB_DIR / f'cdf_slash_2026_{name}.json').read_text(encoding='utf-8'))


class FakeClient:
    """Stand-in for LiquipediaClient serving recorded payloads."""

    def __init__(self, tables=None, matches=None, error=None):
        """Serve `tables` and `matches`, or raise `error` on any call."""
        self.tables = _load('standingstable') if tables is None else tables
        self.matches_payload = _load('match') if matches is None else matches
        self.error = error
        self.standings_tables = SimpleNamespace(list=self._list_tables)
        self.matches = SimpleNamespace(paginate=self._paginate)

    def _list_tables(self, wiki, **kwargs):
        """Mimic `client.standings_tables.list`: one response holding the recorded tables."""
        if self.error:
            raise self.error
        return ApiResponse(result=self.tables, warnings=[])

    def _paginate(self, wiki, **kwargs):
        """Mimic `client.matches.paginate`: iterate over the recorded matches."""
        if self.error:
            raise self.error
        return iter(self.matches_payload)


@pytest.fixture
def cdf():
    """The committed Coupe de France Slash 2026 format."""
    return load_league('cdf_slash_2026')


def _match(match_id):
    """Return a recorded match by id."""
    matches = {item['match2id']: item for item in _load('match')}
    return Match.model_validate(matches[match_id])


# === fetch ===


def test_fetch_cdf_league_play(cdf, caplog):
    """Test that only the 30 League Play series are kept, in round order, with no tiebreaker warning."""
    with caplog.at_level(logging.WARNING):
        series = LiquipediaSource(FakeClient()).fetch(cdf)
    assert len(series) == 30
    assert [item.round for item in series] == [day for day in range(1, 7) for _ in range(5)]
    assert sum(item.finished for item in series) == 8
    assert series[0].teams == ('Gentle Mates', 'Karmine Corp')
    assert not caplog.records


def test_fetch_warns_on_tiebreaker_mismatch(caplog):
    """Test that tiebreakers differing from Liquipedia's are logged."""
    with caplog.at_level(logging.WARNING):
        LiquipediaSource(FakeClient()).fetch(make_league(('matchscore', 'gamediff')))
    assert 'differ from league.json' in caplog.text


@pytest.mark.parametrize(
    ('client', 'message'),
    [
        (FakeClient(error=LiquipediaError('boom')), 'request failed'),
        (FakeClient(tables=[]), 'No standings table'),
        (FakeClient(matches=[]), 'missing'),
    ],
)
def test_fetch_errors(cdf, client, message):
    """Test that API failures and incomplete payloads are data source errors."""
    with pytest.raises(DataSourceError, match=message):
        LiquipediaSource(client).fetch(cdf)


def test_fetch_table_without_rounds(cdf):
    """Test that a standings table listing no rounds is rejected."""
    table = _load('standingstable')[0] | {'extradata': {}}
    with pytest.raises(DataSourceError, match='no rounds'):
        LiquipediaSource(FakeClient(tables=[table])).fetch(cdf)


# === to_series ===


def test_to_series_finished(cdf):
    """Test a finished series with its per-game goals."""
    series = to_series(_match('dprh3EmctD_0001'), 1, cdf)
    assert (series.teams, series.score_1, series.score_2, series.finished) == (
        ('Gentle Mates', 'Karmine Corp'),
        3,
        0,
        True,
    )
    assert series.goals == ((7, 2), (2, 0), (4, 1))


def test_to_series_upcoming(cdf):
    """Test that an upcoming series (scores -1, empty games) is 0-0 with no goals."""
    series = to_series(_match('KYsrR25uHZ_0001'), 3, cdf)
    assert (series.score_1, series.score_2, series.finished, series.goals) == (0, 0, False, ())


@pytest.mark.parametrize(('statuses', 'scores'), [(('W', 'FF'), (3, 0)), (('DQ', 'W'), (0, 3)), (('L', ''), (0, 3))])
def test_to_series_walkover(cdf, statuses, scores):
    """Test that a walkover credits `walkover_games` to the winner, with no goals."""
    match = _match('KYsrR25uHZ_0001')
    opponents = [
        opponent | {'status': status} for opponent, status in zip(match.match2opponents, statuses, strict=True)
    ]
    series = to_series(match.model_copy(update={'match2opponents': opponents, 'finished': 1}), 3, cdf)
    assert ((series.score_1, series.score_2), series.goals, series.finished) == (scores, (), True)


def test_to_series_requires_named_opponents(cdf):
    """Test that a match with an undetermined opponent (TBD) is rejected."""
    with pytest.raises(DataSourceError, match='two named opponents'):
        to_series(_match('xsUsGxjSqt_R01-M001'), 1, cdf)


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


def test_to_series_partial_goals_are_unknown(cdf):
    """Test that a finished series with an unscored game has unknown goals instead of partial ones."""
    match = _match('dprh3EmctD_0001')
    games = [dict(game) for game in match.match2games]
    games[1] |= {'opponents': [{'status': 'S', 'score': 2}, {'status': '', 'score': 0}]}
    series = to_series(match.model_copy(update={'match2games': games}), 1, cdf)
    assert (series.score_1, series.score_2, series.goals) == (3, 0, None)
