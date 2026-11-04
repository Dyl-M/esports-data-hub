"""Tests for edh/scenarios/sources/liquipedia.py - LPDB matches to series (recorded payloads, no live calls)."""

# Standard library
import json
import logging

# Third-party
import pytest
from liquipydia import ApiResponse, LiquipediaError, Match

# Local
from edh import paths
from edh.exceptions import ConfigurationError, DataSourceError
from edh.scenarios.config import load_league
from edh.scenarios.models import EventInfo
from edh.scenarios.sources import liquipedia
from edh.scenarios.sources.liquipedia import LiquipediaSource, load_api_key, parse_organizers, to_series

from .conftest import FIXTURES_DIR, make_league

LPDB_DIR = FIXTURES_DIR / 'lpdb'


def _load(name):
    """Load a recorded LPDB payload."""
    return json.loads((LPDB_DIR / f'cdf_slash_2026_{name}.json').read_text(encoding='utf-8'))


class FakeStandingsTables:
    """Recorded `/standingstable` responses, matching the source's `LpdbStandingsTables` protocol."""

    def __init__(self, tables, error):
        """Serve `tables`, or raise `error` when set; queries are recorded in `queries`."""
        self.tables = tables
        self.error = error
        self.queries = []

    def list(self, wiki, *, pagename, order=None):
        """Record the query, then return one response holding the recorded tables."""
        self.queries.append({'wiki': wiki, 'pagename': pagename, 'order': order})
        if self.error:
            raise self.error
        return ApiResponse(result=self.tables, warnings=[])


class FakeMatches:
    """Recorded `/match` responses, matching the source's `LpdbMatches` protocol."""

    def __init__(self, matches, error):
        """Serve `matches`, or raise `error` when set; queries are recorded in `queries`."""
        self.matches = matches
        self.error = error
        self.queries = []

    def paginate(self, wiki, *, pagename, order=None, page_size=50):
        """Record the query, then iterate over the recorded matches."""
        self.queries.append({'wiki': wiki, 'pagename': pagename, 'order': order, 'page_size': page_size})
        if self.error:
            raise self.error
        return iter(self.matches)


class FakeTournaments:
    """Recorded `/tournament` responses, matching the source's `LpdbTournaments` protocol."""

    def __init__(self, tournaments, error):
        """Serve `tournaments`, or raise `error` when set; queries are recorded in `queries`."""
        self.tournaments = tournaments
        self.error = error
        self.queries = []

    def list(self, wiki, *, pagename, query=None):
        """Record the query, then return one response holding the recorded tournaments."""
        self.queries.append({'wiki': wiki, 'pagename': pagename, 'query': query})
        if self.error:
            raise self.error
        return ApiResponse(result=self.tournaments, warnings=[])


class FakeClient:
    """Stand-in for LiquipediaClient serving recorded payloads, matching the source's `LpdbClient` protocol."""

    def __init__(self, tables=None, matches=None, error=None, tournaments=None):
        """Serve `tables`, `matches` and `tournaments` (recorded CdF payloads by default), or raise `error`."""
        self.standings_tables = FakeStandingsTables(_load('standingstable') if tables is None else tables, error)
        self.matches = FakeMatches(_load('match') if matches is None else matches, error)
        self.tournaments = FakeTournaments(_load('tournament') if tournaments is None else tournaments, error)
        self.closed = False

    def __enter__(self):
        """Mimic the client context manager."""
        return self

    def __exit__(self, *exc_info):
        """Record that the client was closed."""
        self.closed = True


@pytest.fixture
def cdf():
    """The committed Coupe de France Slash 2026 format."""
    return load_league('cdf_slash_2026')


def _match_payload(match_id):
    """Return the raw recorded payload of a match, as a dict that tests can edit before validating it."""
    matches = {item['match2id']: item for item in _load('match')}
    return matches[match_id]


def _match(match_id):
    """Return a recorded match by id."""
    return Match.model_validate(_match_payload(match_id))


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


def test_fetch_queries(cdf):
    """Test that the source queries the league's page on its wiki: tables by index, matches by date, 100 per page."""
    client = FakeClient()
    LiquipediaSource(client).fetch(cdf)
    page = {'wiki': 'rocketleague', 'pagename': 'NEO/Coupe_de_France_Slash/2026'}
    assert client.standings_tables.queries == [page | {'order': 'standingsindex ASC'}]
    assert client.matches.queries == [page | {'order': 'date ASC', 'page_size': 100}]


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


def test_fetch_opens_and_closes_its_own_client(cdf, monkeypatch):
    """Test that without an injected client, fetch opens one with the configured key and closes it afterwards."""
    calls = []

    def open_client(app_name, *, api_key):
        """Stand in for the LiquipediaClient constructor, recording how it was called."""
        client = FakeClient()
        calls.append({'app_name': app_name, 'api_key': api_key, 'client': client})
        return client

    monkeypatch.setenv('LPDB_API_KEY', 'env-key')
    monkeypatch.setattr(liquipedia, 'LiquipediaClient', open_client)
    series = LiquipediaSource().fetch(cdf)
    assert len(series) == 30
    [call] = calls
    assert (call['app_name'], call['api_key'], call['client'].closed) == ('esports-data-hub', 'env-key', True)


# === fetch_event ===


def test_fetch_event_cdf(cdf):
    """Test that the organizers come from the league page's tournament record."""
    client = FakeClient()
    assert LiquipediaSource(client).fetch_event(cdf) == EventInfo(organizers=('NEO',))
    assert client.tournaments.queries == [
        {'wiki': 'rocketleague', 'pagename': 'NEO/Coupe_de_France_Slash/2026', 'query': 'pagename,organizers'}
    ]


def test_fetch_event_without_tournament_record(cdf, caplog):
    """Test that a page without a tournament record gives unknown organizers, with a warning."""
    with caplog.at_level(logging.WARNING):
        assert LiquipediaSource(FakeClient(tournaments=[])).fetch_event(cdf) == EventInfo()
    assert 'organizers unknown' in caplog.text


def test_fetch_event_api_error(cdf):
    """Test that an API failure is a data source error."""
    with pytest.raises(DataSourceError, match='request failed'):
        LiquipediaSource(FakeClient(error=LiquipediaError('boom'))).fetch_event(cdf)


def test_fetch_event_opens_its_own_client(cdf, monkeypatch):
    """Test that without an injected client, fetch_event opens one and closes it afterwards."""
    clients = []

    def open_client(app_name, *, api_key):
        """Stand in for the LiquipediaClient constructor."""
        clients.append(FakeClient())
        return clients[-1]

    monkeypatch.setenv('LPDB_API_KEY', 'env-key')
    monkeypatch.setattr(liquipedia, 'LiquipediaClient', open_client)
    assert LiquipediaSource().fetch_event(cdf).organizers == ('NEO',)
    assert clients[0].closed


@pytest.mark.parametrize(
    ('text', 'organizers'),
    [
        ('{"organizer1":"NEO"}', ('NEO',)),
        ('{"organizer10":"J","organizer2":"B","organizer1":"A"}', ('A', 'B', 'J')),
        ('{"organizer1":"A","organizer2":""}', ('A',)),
        ('', ()),
        (None, ()),
        ('[]', ()),
        ('{not json', ()),
    ],
)
def test_parse_organizers(text, organizers):
    """Test that organizers are read in numeric key order, empty or unreadable values giving none."""
    assert parse_organizers(text) == organizers


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
    payload = _match_payload('KYsrR25uHZ_0001')
    opponents = [
        opponent | {'status': status} for opponent, status in zip(payload['match2opponents'], statuses, strict=True)
    ]
    series = to_series(Match.model_validate(payload | {'match2opponents': opponents, 'finished': 1}), 3, cdf)
    assert ((series.score_1, series.score_2), series.goals, series.finished) == (scores, (), True)


def test_to_series_partial_goals_are_unknown(cdf):
    """Test that a finished series with an unscored game has unknown goals instead of partial ones."""
    payload = _match_payload('dprh3EmctD_0001')
    games = [dict(game) for game in payload['match2games']]
    games[1] |= {'opponents': [{'status': 'S', 'score': 2}, {'status': '', 'score': 0}]}
    series = to_series(Match.model_validate(payload | {'match2games': games}), 1, cdf)
    assert (series.score_1, series.score_2, series.goals) == (3, 0, None)


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
