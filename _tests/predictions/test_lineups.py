"""Tests for edh/predictions/lineups.py - lineup identity over a match history."""

# Standard library
import datetime as dt

# Third-party
import pytest

# Local
from edh.predictions.constants import CARRYOVER_DAYS, KIND_CLUB, KIND_COLLEGE, KIND_NATIONAL
from edh.predictions.lineups import identify_lineups, season_of, side_kind

from .conftest import make_match, make_opponent

START = dt.datetime(2026, 1, 1, 18, tzinfo=dt.UTC)
RIVAL = make_opponent('Rival', 0, 'r1 r2 r3')  # the other side of every match, never sharing a player
COLLEGE_PAGE = 'Collegiate_Rocket_League/2026/Spring'


def _match(day, name, players, *, national=False, page='Some/Page'):
    """A match on day (from START) of name with players (space-separated pages) against the rival, won by name."""
    winner = make_opponent(name, 3, players, national=national)
    return make_match(winner, RIVAL, match_id=f'{day:04d}_{name}', played_at=START + dt.timedelta(days=day), page=page)


def _lineups(*matches):
    """Lineup of the first side of each match, in the order given."""
    lineups = identify_lineups(matches)
    return [lineups.of(match)[0] for match in matches]


# === Same name ===


def test_core_kept_continues_the_lineup():
    """Test that subs chain along while 2 players stay together, a sub listed as a fourth player included."""
    lineups = _lineups(
        _match(0, 'Team', 'a b c'),
        _match(10, 'Team', 'a b d'),
        _match(20, 'Team', 'a b c d'),
        _match(30, 'Team', 'c d e'),
    )
    assert {lineup.lineup_id for lineup in lineups} == {lineups[0].lineup_id}


def test_core_kept_over_any_gap():
    """Test that a name keeping its core stays the same lineup after a long break (an off-season)."""
    first, later = _lineups(_match(0, 'Team', 'a b c'), _match(CARRYOVER_DAYS + 200, 'Team', 'a b c'))
    assert later == first


def test_core_lost_starts_a_new_lineup():
    """Test that a name with a new core is a new lineup, while the old core carries its own on under another name."""
    old, moved, rebuilt = _lineups(
        _match(0, 'Team', 'a b c'),
        _match(10, 'Moved', 'a b x'),
        _match(20, 'Team', 'c e f'),
    )
    assert moved.predecessor == old.lineup_id
    assert rebuilt.lineup_id not in {old.lineup_id, moved.lineup_id}
    assert rebuilt.predecessor is None


# === Predecessors ===


@pytest.mark.parametrize(('gap', 'inherits'), [(CARRYOVER_DAYS, True), (CARRYOVER_DAYS + 1, False)])
def test_rename_inherits_within_the_carryover(gap, inherits):
    """Test that a new name with a core inherits from a lineup that played within `CARRYOVER_DAYS` only."""
    old, new = _lineups(_match(0, 'G2', 'a b c'), _match(gap, 'NRG', 'a b c'))
    assert new.lineup_id != old.lineup_id
    assert new.predecessor == (old.lineup_id if inherits else None)


def test_concurrent_squads_never_merge():
    """Test that a national team inherits from its players' club, both then going on as separate lineups."""
    club, nation, club_again = _lineups(
        _match(0, 'FURIA', 'a b c'),
        _match(5, 'Brazil', 'a b c', national=True),
        _match(10, 'FURIA', 'a b c'),
    )
    assert (nation.kind, nation.predecessor) == (KIND_NATIONAL, club.lineup_id)
    assert club_again == club


@pytest.mark.parametrize('other', [{'national': True}, {'page': COLLEGE_PAGE}], ids=[KIND_NATIONAL, KIND_COLLEGE])
def test_club_never_inherits_from_national_or_college(other):
    """Test that a club never starts from a national or college lineup."""
    _, club = _lineups(_match(0, 'Squad', 'a b c', **other), _match(5, 'Club', 'a b c'))
    assert club.predecessor is None


def test_predecessor_choice():
    """Test the order: club lineups first, then most players in common, then the latest."""
    club, other, nation, rename, team_brazil = _lineups(
        _match(0, 'Club', 'a b c'),
        _match(5, 'Other', 'a b d'),
        _match(8, 'Brazil', 'a b c', national=True),
        _match(10, 'New', 'a b c'),
        _match(12, 'Team Brazil', 'a b c', national=True),
    )
    assert (other.predecessor, nation.predecessor) == (club.lineup_id, club.lineup_id)
    assert rename.predecessor == club.lineup_id  # 3 in common, over Other's 2 and Brazil (national)
    assert team_brazil.predecessor == rename.lineup_id  # clubs first, then the latest of the 3-in-common ones


# === Without roster ===


@pytest.mark.parametrize(('gap', 'same'), [(CARRYOVER_DAYS, True), (CARRYOVER_DAYS + 1, False)])
def test_side_without_roster_goes_to_its_recent_name(gap, same):
    """Test that a side without roster joins its name's lineup when recent, else a new lineup without predecessor."""
    first, unknown = _lineups(_match(0, 'Team', 'a b c'), _match(gap, 'Team', 'a'))
    assert (unknown == first) is same
    assert unknown.predecessor is None


def test_lineup_without_roster_is_taken_over():
    """Test that the next roster under a name takes its roster-less lineup over, whatever the players."""
    unknown, known, next_core = _lineups(
        _match(0, 'Team', ''), _match(CARRYOVER_DAYS + 50, 'Team', 'a b c'), _match(200, 'Team', 'a b d')
    )
    assert known == unknown == next_core


def test_side_without_roster_keeps_its_lineup_recent():
    """Test that a match without roster counts as played, so the name's lineup stays recent."""
    lineups = _lineups(
        _match(0, 'Team', 'a b c'),
        _match(100, 'Team', ''),
        _match(200, 'Team', ''),
        _match(280, 'Renamed', 'a b c'),
    )
    assert lineups[0] == lineups[1] == lineups[2]
    assert lineups[3].predecessor == lineups[0].lineup_id


def test_core_back_after_a_lineup_without_roster():
    """Test that a roster bringing the name's last core back continues that lineup, not the roster-less one."""
    known, unknown, back = _lineups(
        _match(0, 'Team', 'a b c'), _match(CARRYOVER_DAYS + 80, 'Team', ''), _match(210, 'Team', 'a b d')
    )
    assert unknown != known
    assert back == known


# === Whole history ===


def test_players_who_left_no_longer_count():
    """Test that only the latest roster of a lineup is compared: players who left bring no core with them."""
    lineups = _lineups(
        _match(0, 'Team', 'a b c'),
        _match(10, 'Team', 'a b d'),
        _match(20, 'Left', 'c x y'),
        _match(30, 'Mixed', 'c d z'),
    )
    assert lineups[2].predecessor is None
    assert lineups[3].predecessor is None  # c and d were never together in Team's latest roster


def test_two_players_are_a_usable_roster():
    """Test that a roster of exactly 2 players replaces the lineup's latest roster."""
    lineups = _lineups(_match(0, 'Team', 'a b c'), _match(10, 'Team', 'a b'), _match(20, 'Renamed', 'b c x'))
    assert lineups[1] == lineups[0]
    assert lineups[2].predecessor is None  # c left with the 2-player roster: only b is in common


def test_sides_of_a_match_placed_independently():
    """Test that a side never inherits from the other side of the same match (players listed on both sides)."""
    match = make_match(make_opponent('A', 3, 'a b c'), make_opponent('B', 0, 'a b c'))
    first, second = identify_lineups([match]).of(match)
    assert (first.predecessor, second.predecessor) == (None, None)


def test_matches_taken_in_date_order():
    """Test that the input order does not matter, and both sides of every match get a lineup."""
    matches = [_match(0, 'G2', 'a b c'), _match(30, 'NRG', 'a b c')]
    lineups = identify_lineups(reversed(matches))
    assert [lineup.name for lineup in lineups.lineups] == ['G2', 'Rival', 'NRG']
    assert lineups.sides == {'0000_G2': (0, 1), '0030_NRG': (2, 1)}
    assert lineups.lineups[2].predecessor == 0


# === Kinds and seasons ===


@pytest.mark.parametrize(
    ('national', 'page', 'kind'),
    [
        (False, 'Rocket_League_Championship_Series/2026', KIND_CLUB),
        (True, 'FIFAe_World_Cup/2025/Group_A', KIND_NATIONAL),
        (False, COLLEGE_PAGE, KIND_COLLEGE),
        (True, COLLEGE_PAGE, KIND_NATIONAL),
    ],
)
def test_side_kind(national, page, kind):
    """Test that a flag icon makes a national team, a college event's page a college team, anything else a club."""
    match = _match(0, 'Team', 'a b c', national=national, page=page)
    assert side_kind(match, match.opponents[0]) == kind


@pytest.mark.parametrize(
    ('day', 'season'),
    [
        (dt.date(2024, 9, 15), 2024),
        (dt.date(2024, 9, 16), 2025),
        (dt.date(2025, 9, 12), 2025),
        (dt.date(2025, 9, 13), 2026),
        (dt.date(2026, 11, 11), 2027),
    ],
)
def test_season_of(day, season):
    """Test that a season starts the day after the previous World Championship final."""
    assert season_of(day) == season
