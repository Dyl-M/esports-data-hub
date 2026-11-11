"""Tests for edh/predictions/history.py - LPDB history cache (fake client, no live calls)."""

# Standard library
import datetime as dt
import json

# Third-party
import pytest
from liquipydia import Match, NotFoundError, RateLimitError

# Local
from edh import paths
from edh.exceptions import DataSourceError
from edh.predictions import history
from edh.predictions.constants import HISTORY_START, MATCH_FIELDS, REFETCH_DAYS
from edh.predictions.history import (
    HistoryScope,
    history_dir,
    page_file,
    read_history,
    to_played_match,
    update_history,
)
from edh.predictions.models import Player

from ..conftest import FIXTURES_DIR
from .conftest import PAGE, TODAY, FakeClient, make_record

RECORDED = FIXTURES_DIR / 'lpdb' / 'rlcs_2026_history_match.json'


def _update(folder, records=(), *, error=None, **kwargs):
    """Run an update on folder with a fake client; return the client and the update."""
    client = FakeClient(records, error)
    return client, update_history(client, today=TODAY, cache_dir=folder, **kwargs)


def _manifest(folder):
    """The manifest written in folder."""
    return json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))


def _cached(folder, page=PAGE):
    """The records cached for a page."""
    return json.loads(page_file(folder, page).read_text(encoding='utf-8'))['matches']


# === Scope ===


def test_scope_conditions():
    """Test that the conditions take whole days (LPDB compares strictly) and every tier."""
    scope = HistoryScope(start=dt.date(2024, 9, 1), tiers=('1', '2'))
    assert scope.conditions(dt.date(2024, 9, 1), TODAY) == (
        '[[date::>2024-08-31]] AND [[date::<2026-11-11]] AND ([[liquipediatier::1]] OR [[liquipediatier::2]])'
    )


@pytest.mark.parametrize('tiers', [(), ('1', '')])
def test_scope_needs_tiers(tiers: tuple[str, ...]):
    """Test that a scope without tiers, or with an empty one, is rejected."""
    with pytest.raises(ValueError, match='tiers'):
        HistoryScope(tiers=tiers)


def test_history_dir_reads_paths_at_call_time(monkeypatch, tmp_path):
    """Test that the cache folder follows `paths.LPDB_CACHE_DIR` and the wiki."""
    monkeypatch.setattr(paths, 'LPDB_CACHE_DIR', tmp_path)
    assert history_dir('rocketleague') == tmp_path / 'rocketleague'


def test_page_file_is_percent_encoded(tmp_path):
    """Test that a page name with slashes maps to one file of the pages folder."""
    assert page_file(tmp_path, 'A/B C') == tmp_path / 'pages' / 'A%2FB%20C.json'


# === Update ===


def test_first_update_fetches_the_scope(tmp_path):
    """Test a first run: from the scope start to today, the allowlisted fields, by date, 1000 per request."""
    records = [make_record('m1', '2026-05-20'), make_record('m2', '2026-05-21', pagename='Other/Page')]
    client, update = _update(tmp_path, records)
    assert client.matches.queries == [
        {
            'wiki': 'rocketleague',
            'conditions': HistoryScope().conditions(HISTORY_START, TODAY),
            'query': ','.join(MATCH_FIELDS),
            'order': 'date ASC',
            'size': 1000,
        }
    ]
    assert (update.since, update.until, update.matches, update.pages) == (HISTORY_START, TODAY, 2, 2)
    assert [record['match2id'] for record in _cached(tmp_path)] == ['m1']
    assert [record['match2id'] for record in _cached(tmp_path, 'Other/Page')] == ['m2']
    assert _manifest(tmp_path) | {'updated_at': None} == {
        'wiki': 'rocketleague',
        'start': HISTORY_START.isoformat(),
        'tiers': ['1', '2'],
        'synced_through': TODAY.isoformat(),
        'updated_at': None,
    }


def test_next_update_refetches_recent_days_and_replaces_matches(tmp_path):
    """Test that a later run starts `REFETCH_DAYS` before the last synced day and replaces a refetched match."""
    _update(tmp_path, [make_record('m1', '2026-05-20', (2, 3)), make_record('m0', '2026-05-19')])
    client, update = _update(tmp_path, [make_record('m1', '2026-05-20', (3, 2))])
    assert update.since == TODAY - dt.timedelta(days=REFETCH_DAYS)
    assert client.matches.queries[0]['conditions'].startswith(f'[[date::>{update.since - dt.timedelta(days=1)}]]')
    cached = _cached(tmp_path)
    assert [record['match2id'] for record in cached] == ['m0', 'm1']  # sorted by date
    assert [opponent['score'] for opponent in cached[1]['match2opponents']] == [3, 2]


@pytest.mark.parametrize('change', ['refetch', 'scope'])
def test_refetch_or_new_scope_starts_over(tmp_path, change):
    """Test that `refetch` or a different scope fetches again from the scope start."""
    _update(tmp_path, [make_record('m1', '2026-05-20')])
    scope = HistoryScope(tiers=('1',)) if change == 'scope' else None
    _, update = _update(tmp_path, refetch=change == 'refetch', scope=scope)
    assert update.since == HISTORY_START


def test_match_fields_are_lpdb_fields():
    """Test that every queried field exists in LPDB's match model."""
    assert set(MATCH_FIELDS) <= set(Match.model_fields)


def test_update_without_matches(tmp_path, caplog):
    """Test that an empty result is an empty update, still synced to today, with a warning."""
    _, update = _update(tmp_path)
    assert (update.matches, update.pages) == (0, 0)
    assert _manifest(tmp_path)['synced_through'] == TODAY.isoformat()
    assert 'returned no match' in caplog.text


def test_interrupted_update_keeps_its_progress(tmp_path, monkeypatch):
    """Test that a rate limit mid-run saves every match received, records the last day reached, and resumes there."""
    monkeypatch.setattr(history, 'LPDB_MAX_PAGE_SIZE', 2)
    records = [make_record(f'm{day}', f'2025-01-{day:02d}') for day in (1, 2, 3, 4)]
    with pytest.raises(DataSourceError, match=r'after 4 matches.*run again'):
        _update(tmp_path, records, error=RateLimitError('Rate limit exceeded'))
    assert len(_cached(tmp_path)) == 4
    assert _manifest(tmp_path)['synced_through'] == '2025-01-04'  # the last day of the last page saved
    _, update = _update(tmp_path)
    assert update.since == dt.date(2025, 1, 4) - dt.timedelta(days=REFETCH_DAYS)


def test_records_come_in_full_pages(monkeypatch):
    """Test that records are grouped by LPDB page, the last one possibly partial."""
    monkeypatch.setattr(history, 'LPDB_MAX_PAGE_SIZE', 2)
    records = [make_record(f'm{day}', f'2025-01-{day:02d}') for day in (1, 2, 3)]
    batches = history._batches(FakeClient(records), HistoryScope(), HISTORY_START, TODAY)
    assert [len(batch) for batch in batches] == [2, 1]


def test_full_last_page(tmp_path, monkeypatch):
    """Test that a last page exactly full is saved once, with the run complete."""
    monkeypatch.setattr(history, 'LPDB_MAX_PAGE_SIZE', 2)
    _, update = _update(tmp_path, [make_record('m1', '2025-01-01'), make_record('m2', '2025-01-02')])
    assert (update.matches, update.pages) == (2, 1)
    assert _manifest(tmp_path)['synced_through'] == TODAY.isoformat()


def test_refetch_window_stops_at_the_scope_start(tmp_path):
    """Test that the refetch window never reaches before the scope start."""
    _update(tmp_path, scope=HistoryScope(start=TODAY))
    _, update = _update(tmp_path, scope=HistoryScope(start=TODAY))
    assert update.since == TODAY


def test_broken_query_is_an_error(tmp_path):
    """Test that a 404 (LPDB's answer to a broken query, not to an empty one) stops the update."""
    with pytest.raises(DataSourceError, match='after 0 matches'):
        _update(tmp_path, error=NotFoundError('Requested data does not exist'))
    assert not (tmp_path / 'manifest.json').exists()


def test_no_temporary_file_left(tmp_path):
    """Test that files are written through a temporary file that does not remain."""
    _update(tmp_path, [make_record('m1', '2026-05-20')])
    assert not list(tmp_path.rglob('*.tmp'))


def test_cache_keeps_names_readable(tmp_path):
    """Test that non-ASCII names are stored as UTF-8 text, not escaped (cache files stay readable and smaller)."""
    _update(tmp_path, [make_record('m1', '2026-05-20', names=('Équipe A', 'Team B'))])
    assert '"Équipe A"' in page_file(tmp_path, PAGE).read_text(encoding='utf-8')


# === Read ===


def test_read_history_parses_sorts_and_filters(tmp_path):
    """Test that only finished team matches with a winner are read, oldest first."""
    records = [
        make_record('late', '2026-05-21'),
        make_record('early', '2026-05-20', pagename='Other/Page'),
        make_record('duel', '2026-05-20', mode='1v1'),
        make_record('pending', '2026-05-22', finished=0),
        make_record('tbd', '2026-05-22', names=('Team A', '')),
        make_record('tie', '2026-05-22', (1, 1)),
        make_record('self', '2026-05-22', names=('Team A', 'Team A')),
    ]
    _update(tmp_path, records)
    assert [match.match_id for match in read_history(cache_dir=tmp_path)] == ['early', 'late']


def test_read_history_keeps_the_scope(tmp_path):
    """Test that records cached under a wider scope are left out: other tiers, days before the start."""
    records = [
        make_record('kept', '2026-05-20'),
        make_record('tier 2', '2026-05-20', liquipediatier='2'),
        make_record('too early', '2026-05-19'),
    ]
    _update(tmp_path, records)
    scope = HistoryScope(start=dt.date(2026, 5, 20), tiers=('1',))
    assert [match.match_id for match in read_history(scope, cache_dir=tmp_path)] == ['kept']


def test_read_history_default_folder(tmp_path, monkeypatch):
    """Test that the default folder is the default wiki's under `paths.LPDB_CACHE_DIR`."""
    monkeypatch.setattr(paths, 'LPDB_CACHE_DIR', tmp_path)
    _update(tmp_path / 'rocketleague', [make_record('m1', '2026-05-20')])
    assert [match.match_id for match in read_history()] == ['m1']
    assert read_history(cache_dir=tmp_path / 'missing') == []


def test_to_played_match_fields():
    """Test the conversion: UTC time, tier, LAN, rosters, decided games."""
    match = to_played_match(Match.model_validate(make_record('m1', '2026-05-20', (3, 1))))
    assert match.played_at == dt.datetime(2026, 5, 20, 15, tzinfo=dt.UTC)
    assert (match.page, match.tier, match.lan, match.walkover, match.winner) == (PAGE, '1', True, False, 0)
    assert match.opponents[0].players == (
        Player('Team A_p1', 'Team A 1', 'France'),
        Player('Team A_p2', 'Team A 2', 'France'),
    )
    assert match.opponents[1].template == 'team b'
    assert match.games == ((2, 1), (2, 1), (2, 1), (0, 1))


@pytest.mark.parametrize(('match_type', 'lan'), [('Offline', True), ('Online', False), ('', None)])
def test_to_played_match_lan(match_type, lan):
    """Test that `type` tells LAN from online, and nothing when empty."""
    assert to_played_match(Match.model_validate(make_record('m1', '2026-05-20', type=match_type))).lan is lan


@pytest.mark.parametrize(('statuses', 'scores'), [(('W', 'FF'), (1, 0)), (('FF', 'W'), (0, 1)), (('S', 'DQ'), (1, 0))])
def test_to_played_match_walkover(statuses, scores):
    """Test that a walkover scores 1-0 for its winner, without games."""
    match = to_played_match(Match.model_validate(make_record('m1', '2026-05-20', (0, 0), statuses=statuses)))
    assert match.walkover
    assert (match.opponents[0].score, match.opponents[1].score) == scores
    assert match.games == ()


def test_to_played_match_sweep_and_unnamed_player():
    """Test that a team without a game won scores 0, and a roster entry without player page is left out."""
    record = make_record('m1', '2026-05-20', (0, 3))
    record['match2opponents'][0]['match2players'].append({'displayname': 'Unknown'})
    match = to_played_match(Match.model_validate(record))
    assert (match.opponents[0].score, match.opponents[1].score, match.winner) == (0, 3, 1)
    assert [player.page for player in match.opponents[0].players] == ['Team A_p1', 'Team A_p2']


def test_to_played_match_national():
    """Test that a team with a flag as its icon is a national team, any other one is not."""
    record = make_record('m1', '2026-05-20')
    record['match2opponents'][0]['icon'] = 'Flag of France 4 by 3.png'
    record['match2opponents'][1]['icon'] = 'Team B allmode.png'
    match = to_played_match(Match.model_validate(record))
    assert [opponent.national for opponent in match.opponents] == [True, False]


def test_to_played_match_without_date():
    """Test that a match without a date (LPDB's null date) is skipped."""
    assert to_played_match(Match.model_validate(make_record('m1', '0000-01-01'))) is None


def test_recorded_rlcs_match():
    """Test the conversion of a recorded RLCS 2026 match (real LPDB shape, `MATCH_FIELDS` only)."""
    record = json.loads(RECORDED.read_text(encoding='utf-8'))
    assert set(record) <= set(MATCH_FIELDS)
    match = to_played_match(Match.model_validate(record))
    assert match.tier == '1'
    assert all(len(opponent.players) == 3 and not opponent.national for opponent in match.opponents)
    assert len(match.games) == match.opponents[0].score + match.opponents[1].score
