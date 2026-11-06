"""Tests for edh/scenarios/sources/liquipedia.py - LPDB matches to series (recorded payloads, no live calls)."""

# Standard library
import datetime as dt
import json
import logging
from dataclasses import replace

# Third-party
import pytest
from liquipydia import ApiResponse, LiquipediaError, Match, TeamTemplate

# Local
from edh import paths
from edh.exceptions import ConfigurationError, DataSourceError
from edh.scenarios.config import load_league
from edh.scenarios.sources import liquipedia
from edh.scenarios.sources.liquipedia import (
    LiquipediaSource,
    load_api_key,
    parse_organizers,
    parse_utc_offset,
    team_logo_url,
    to_series,
)

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


class FakeTeamTemplates:
    """Recorded `/teamtemplate` responses, matching the source's `LpdbTeamTemplates` protocol."""

    def __init__(self, templates, error):
        """Serve `templates` (template name -> records), or raise `error` when set; queries are recorded."""
        self.templates = templates
        self.error = error
        self.queries = []

    def get(self, wiki, template, *, date=None):
        """Record the query, then return the recorded records of the template (none when unknown)."""
        self.queries.append({'wiki': wiki, 'template': template, 'date': date})
        if self.error:
            raise self.error
        return ApiResponse(result=self.templates.get(template, []), warnings=[])


class FakeClient:
    """Stand-in for LiquipediaClient serving recorded payloads, matching the source's `LpdbClient` protocol."""

    def __init__(self, tables=None, matches=None, error=None, tournaments=None, templates=None):
        """Serve `tables`, `matches`, `tournaments` and `templates` (recorded CdF payloads by default), or `error`."""
        self.standings_tables = FakeStandingsTables(_load('standingstable') if tables is None else tables, error)
        self.matches = FakeMatches(_load('match') if matches is None else matches, error)
        self.tournaments = FakeTournaments(_load('tournament') if tournaments is None else tournaments, error)
        self.team_templates = FakeTeamTemplates(_load('teamtemplate') if templates is None else templates, error)
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
    assert len(calls) == 1
    call = calls[0]
    assert (call['app_name'], call['api_key'], call['client'].closed) == ('esports-data-hub', 'env-key', True)


# === fetch_event ===


def test_fetch_event_cdf(cdf):
    """Test the event details: organizers and dark competition logo from the tournament, team logos from templates."""
    client = FakeClient()
    event = LiquipediaSource(client).fetch_event(cdf)
    assert event.organizers == ('NEO',)
    assert event.logo_url.endswith('/Coupe_de_France_Slash_icon_darkmode.png')
    assert client.tournaments.queries == [
        {
            'wiki': 'rocketleague',
            'pagename': 'NEO/Coupe_de_France_Slash/2026',
            'query': 'pagename,organizers,iconurl,icondarkurl',
        }
    ]
    assert sorted(event.team_logo_urls) == sorted(
        ['DUMORNAY GOAT', 'Geekay Esports', 'Gentle Mates', 'Ikkaku', 'Karmine Corp', 'Team Vitality']
    )
    # teams without branding of their own keep Liquipedia's default logo of the game
    assert event.team_logo_urls['Ikkaku'].endswith('/400px-Rocket_League_default_darkmode.png')
    assert event.team_logo_urls['Gentle Mates'].endswith('/400px-Gentle_Mates_2024_darkmode.png')
    assert {query['template'] for query in client.team_templates.queries} == set(_load('teamtemplate'))


def test_fetch_event_team_logos_use_aliases_and_latest_template(cdf):
    """Test that a renamed team gets the logo of its latest match, under its current name."""
    first, second = (_match_payload(match['match2id']) for match in _load('match')[:2])
    first['match2opponents'][0] |= {'name': 'Old Name', 'template': 'old'}
    second['match2opponents'][0] |= {'name': 'New Name', 'template': 'new'}
    templates = {
        'old': [{'template': 'old', 'imagedark': 'Old darkmode.png', 'imagedarkurl': 'https://x/old.png'}],
        'new': [{'template': 'new', 'imagedark': 'New darkmode.png', 'imagedarkurl': 'https://x/new.png'}],
    }
    league = replace(cdf, team_aliases={'Old Name': 'New Name'})
    event = LiquipediaSource(FakeClient(matches=[first, second], templates=templates)).fetch_event(league)
    assert event.team_logo_urls['New Name'] == 'https://x/new.png'
    assert 'Old Name' not in event.team_logo_urls


@pytest.mark.parametrize(
    ('fields', 'url'),
    [
        ({'imagedarkurl': 'dark', 'imageurl': 'light'}, 'dark'),
        ({'imagedarkurl': '', 'imageurl': 'light'}, 'light'),
        (
            {'imagedarkurl': 'https://x/Rocket_League_default_darkmode.png'},
            'https://x/Rocket_League_default_darkmode.png',
        ),
        ({}, ''),
    ],
)
def test_team_logo_url(fields, url):
    """Test the logo choice: dark version first, then light, Liquipedia's default logo included."""
    assert team_logo_url(TeamTemplate.model_validate(fields)) == url


def test_fetch_event_without_tournament_record(cdf, caplog):
    """Test that a page without a tournament record gives unknown organizers, with a warning."""
    with caplog.at_level(logging.WARNING):
        event = LiquipediaSource(FakeClient(tournaments=[])).fetch_event(cdf)
    assert (event.organizers, event.logo_url) == ((), '')
    assert event.team_logo_urls  # team logos do not depend on the tournament record
    assert 'organizers unknown' in caplog.text


def test_fetch_event_api_error(cdf):
    """Test that an API failure is a data source error."""
    with pytest.raises(DataSourceError, match='request failed'):
        LiquipediaSource(FakeClient(error=LiquipediaError('boom'))).fetch_event(cdf)


def test_fetch_event_opens_its_own_client(cdf, monkeypatch):
    """Test that without an injected client, fetch_event opens one with the configured key and closes it afterwards."""
    calls = []

    def open_client(app_name, *, api_key):
        """Stand in for the LiquipediaClient constructor, recording how it was called."""
        client = FakeClient()
        calls.append({'app_name': app_name, 'api_key': api_key, 'client': client})
        return client

    monkeypatch.setenv('LPDB_API_KEY', 'env-key')
    monkeypatch.setattr(liquipedia, 'LiquipediaClient', open_client)
    assert LiquipediaSource().fetch_event(cdf).organizers == ('NEO',)
    assert len(calls) == 1
    call = calls[0]
    assert (call['app_name'], call['api_key'], call['client'].closed) == ('esports-data-hub', 'env-key', True)


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


CET = {'timezoneid': 'CET', 'timezoneoffset': '+01:00'}  # as recorded on CDF Slash 2026 matches


@pytest.mark.parametrize(
    ('fields', 'day'),
    [
        ({'date': '2026-11-02 18:30:00', 'dateexact': 1, 'extradata': CET}, dt.date(2026, 11, 2)),
        (
            {'date': '2026-11-02 23:30:00', 'dateexact': 1, 'extradata': CET},
            dt.date(2026, 11, 3),
        ),  # past midnight in CET
        (
            {'date': '2026-11-03 01:00:00', 'dateexact': 1, 'extradata': {'timezoneoffset': '-05:00'}},
            dt.date(2026, 11, 2),
        ),
        ({'date': '2026-11-02 23:30:00', 'dateexact': 1}, dt.date(2026, 11, 2)),  # no offset: UTC day
        ({'date': '2026-11-02 23:30:00', 'dateexact': 0, 'extradata': CET}, dt.date(2026, 11, 2)),  # day only, no time
        ({'date': '0000-01-01 00:00:00'}, None),
    ],
)
def test_to_series_local_day(cdf, fields, day):
    """Test that a series is dated by its day in the event's timezone, from LPDB's UTC date and offset."""
    payload = _match_payload('dprh3EmctD_0001') | fields
    assert to_series(Match.model_validate(payload), 1, cdf).played_on == day


@pytest.mark.parametrize(
    ('text', 'offset'),
    [
        ('+01:00', dt.timedelta(hours=1)),
        ('-05:30', -dt.timedelta(hours=5, minutes=30)),
        ('+2', dt.timedelta(hours=2)),
        ('+0530', dt.timedelta(hours=5, minutes=30)),
        ('', None),
        (None, None),
    ],
)
def test_parse_utc_offset(text, offset):
    """Test the UTC offsets LPDB records with match dates."""
    assert parse_utc_offset(text) == offset


def test_parse_utc_offset_unreadable(caplog):
    """Test that an unreadable offset is logged and ignored."""
    with caplog.at_level(logging.WARNING):
        assert parse_utc_offset('CET') is None
    assert 'Unreadable timezone offset' in caplog.text


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
