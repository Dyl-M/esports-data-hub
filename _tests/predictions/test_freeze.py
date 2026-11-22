"""Tests for edh/predictions/freeze.py - frozen forecasts of every pair of an event's teams."""

# Standard library
import datetime as dt
import json

# Third-party
import pytest
from liquipydia import LiquipediaError

# Local
from edh.exceptions import ConfigurationError, DataSourceError
from edh.predictions import freeze
from edh.predictions.evaluate import write_json

from .conftest import FakeMatches, make_match, make_opponent, use_history

PAGE = 'Some/Event'
DAY = dt.date(2026, 12, 9)
NOW = dt.datetime(2026, 12, 8, 23, 59, 59, 999_999, tzinfo=dt.UTC)  # the last moment to freeze
START = dt.datetime(2026, 10, 1, 18, tzinfo=dt.UTC)  # RLCS 2027
PARAMS = {
    'uniform': {},
    'elo': {'k': 24, 'shrink': 0.3, 'offset': 0},
    'glicko2': {'tau': 0.5, 'start_rd': 200, 'shrink': 0.3},
    'bradley_terry': {'half_life': 180, 'l2': 1},
}
IDENTITIES = ('name', 'lineup', 'players')


class FakePlacementClient:
    """Stand-in for LiquipediaClient with only `/placement`."""

    def __init__(self, records=(), error=None):
        self.placements = FakeMatches(list(records), error)


def _placement(name, players='', qualifier='Partner Program', **extra):
    """An LPDB placement record of a team, its roster given as space-separated pages (coach and names added)."""
    pages = players.split()
    roster = {f'p{index}': page for index, page in enumerate(pages, 1)}
    roster |= {f'p{index}dn': page.upper() for index, page in enumerate(pages, 1)} | {'c1': 'Coach', 'p9': ''}
    return {'opponentname': name, 'opponentplayers': roster, 'qualifier': qualifier, **extra}


SLOT = {'opponentname': 'TBD', 'opponentplayers': {}, 'qualifier': '', 'placement': '1'}  # a final place
PLACEMENTS = [
    _placement('Team B', 'b3 b1 b2'),
    SLOT,
    _placement('Team A', 'a1 a2 a3', 'EU Open Qualifier'),
    _placement('Team C', 'a1 b1 x9'),  # a new team: two known players and a newcomer
]


def _history():
    """RLCS 2027: Team A beats Team B 3-1 every week from October 1 to December 10, and as the event starts."""
    starts = [START + dt.timedelta(weeks=week) for week in range(11)] + [dt.datetime(2026, 12, 9, tzinfo=dt.UTC)]
    return [
        make_match(
            make_opponent('Team A', 3, 'a1 a2 a3'),
            make_opponent('Team B', 1, 'b1 b2 b3'),
            match_id=f'm{index:02d}',
            played_at=played_at,
        )
        for index, played_at in enumerate(starts)
    ]


LAST_DAY_BEFORE = (START + dt.timedelta(weeks=9)).date().isoformat()  # December 3


@pytest.fixture
def history(monkeypatch, tmp_path):
    """The small history as the cache and every model and identity tuned, `_data/` in a temporary folder."""
    folder = use_history(monkeypatch, tmp_path, freeze, _history)
    results = [
        {'model': model, 'identity': identity, 'params': params}
        for model, params in PARAMS.items()
        for identity in IDENTITIES
    ]
    content = {'tuning_season': 2026, 'evaluation_season': 2027, 'results': results}
    write_json(content, folder / 'predictions' / '2026-11-09' / 'forecast_params.json')
    return folder


# === Teams ===


def test_event_teams_and_rosters():
    """Test that the teams are the placements with a qualifier, sorted, each with its players' pages sorted."""
    client = FakePlacementClient(PLACEMENTS)
    assert freeze.event_teams(client, PAGE) == {
        'Team A': ('a1', 'a2', 'a3'),
        'Team B': ('b1', 'b2', 'b3'),
        'Team C': ('a1', 'b1', 'x9'),
    }
    assert client.placements.queries == [
        {
            'wiki': 'rocketleague',
            'conditions': '[[pagename::Some/Event]]',
            'query': 'opponentname, opponentplayers, qualifier',
            'order': None,
            'size': 1000,
        }
    ]


def test_event_teams_not_all_known():
    """Test that a slot without its team or roster is an error naming each, sorted."""
    records = [
        *PLACEMENTS,
        _placement('TBD', qualifier='EU Open Qualifier'),
        _placement('', 'x1 x2 x3', qualifier='NA Open Qualifier'),
        _placement(None, 'x1 x2 x3', qualifier='SAM Open Qualifier'),
        _placement('Team Z'),
    ]
    expected = 'EU Open Qualifier winner, NA Open Qualifier winner, SAM Open Qualifier winner, Team Z \\(no roster'
    with pytest.raises(DataSourceError, match=f'Some/Event is known yet: {expected}'):
        freeze.event_teams(FakePlacementClient(records), PAGE)


def test_event_without_teams():
    """Test that a page whose placements are all final places is an error."""
    with pytest.raises(DataSourceError, match='No team on Some/Event yet'):
        freeze.event_teams(FakePlacementClient([SLOT]), PAGE)


def test_event_teams_lpdb_error():
    """Test that an LPDB failure becomes a DataSourceError naming the page and the cause."""
    with pytest.raises(DataSourceError, match=r'LPDB placements of Some/Event unavailable \(rate limited\)'):
        freeze.event_teams(FakePlacementClient(PLACEMENTS, LiquipediaError('rate limited')), PAGE)


# === Freezing ===


def test_freeze_every_pair(history, caplog):
    """Test that every pair is forecast with each model and identity, saved with the event, picks and cold starts."""
    path = history / 'predictions' / 'event' / 'frozen.json'
    content = freeze.freeze(FakePlacementClient(PLACEMENTS), PAGE, DAY, path, NOW)
    assert json.loads(path.read_text(encoding='utf-8')) == content
    assert (content['event'], content['forecast_day']) == (PAGE, '2026-12-09')
    assert content['frozen_at'] == '2026-12-08T23:59:59+00:00'
    assert content['history_through'] == LAST_DAY_BEFORE  # the matches of the event's days are left out
    assert 'Never seen under their name (cold starts for the name and lineup identities): Team C' in caplog.text
    assert content['picks'][:2] == [
        {'model': 'uniform', 'identity': 'name', 'params': {}},
        {'model': 'uniform', 'identity': 'lineup', 'params': {}},
    ]
    assert len(content['picks']) == 12
    assert [(pair['first'], pair['second']) for pair in content['pairs']] == [
        ('Team A', 'Team B'),
        ('Team A', 'Team C'),
        ('Team B', 'Team C'),
    ]
    first = content['pairs'][0]['game_probability']
    assert first['uniform'] == {'name': 0.5, 'lineup': 0.5, 'players': 0.5}
    assert list(first) == list(PARAMS)
    assert first['elo']['players'] > 0.5  # Team A always won
    team_c = content['teams'][2]
    assert team_c == {
        'team': 'Team C',
        'roster': ['a1', 'b1', 'x9'],
        'never_rated': {'name': ['Team C'], 'lineup': ['Team C'], 'players': ['x9']},
    }
    assert content['teams'][0]['never_rated'] == {'name': [], 'lineup': [], 'players': []}


def test_freeze_known_teams_without_warning(history, caplog):
    """Test that no warning is logged when every team was seen under its name."""
    content = freeze.freeze(FakePlacementClient(PLACEMENTS[:3]), PAGE, DAY, history / 'frozen.json', NOW)
    assert [team['team'] for team in content['teams']] == ['Team A', 'Team B']
    assert 'Never seen' not in caplog.text


def test_freeze_never_overwrites(history):
    """Test that an existing frozen file is an error, left as it was, before anything is asked of LPDB."""
    path = history / 'frozen.json'
    path.write_text('{}', encoding='utf-8')
    client = FakePlacementClient(PLACEMENTS)
    with pytest.raises(ConfigurationError, match='Already frozen') as error:
        freeze.freeze(client, PAGE, DAY, path, NOW)
    assert error.value.file_path == str(path)
    assert path.read_text(encoding='utf-8') == '{}'
    assert client.placements.queries == []


def test_freeze_too_late(history):
    """Test that freezing once the event's first day has begun is an error, nothing asked or written."""
    path = history / 'frozen.json'
    client = FakePlacementClient(PLACEMENTS)
    with pytest.raises(ConfigurationError, match='Too late to freeze: the event started on 2026-12-09') as error:
        freeze.freeze(client, PAGE, DAY, path, dt.datetime(2026, 12, 9, tzinfo=dt.UTC))
    assert error.value.file_path == str(path)
    assert not path.exists()
    assert client.placements.queries == []


def test_freeze_now_by_default(history):
    """Test that the freeze's time is the current one when not given (too late for an event already started)."""
    with pytest.raises(ConfigurationError, match='Too late'):
        freeze.freeze(FakePlacementClient(PLACEMENTS), PAGE, dt.datetime.now(dt.UTC).date(), history / 'frozen.json')


def test_freeze_without_history_before_the_event(history):
    """Test that a history without any match before the event's day is an error pointing to the update."""
    path = history / 'frozen.json'
    with pytest.raises(
        DataSourceError, match=r'No match cached before 2026-10-01: run `python -m edh.predictions.run`'
    ):
        freeze.freeze(FakePlacementClient(PLACEMENTS), PAGE, START.date(), path, NOW - dt.timedelta(days=90))
    assert not path.exists()
