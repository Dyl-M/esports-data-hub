"""Tests for edh/scenarios/standings.py - team records and Liquipedia-style tiebreakers."""

# Third-party
import pytest

# Local
from edh.scenarios.models import Tiebreaker
from edh.scenarios.standings import rank, tally

from .conftest import make_series


def _tiebreakers(*values):
    """Parse tiebreaker identifiers."""
    return tuple(Tiebreaker.parse(value) for value in values)


def _ranking(groups):
    """Turn rank groups into comparable tuples."""
    return [(group.rank, group.teams) for group in groups]


# === tally ===


def test_tally_counts_finished_series_only():
    """Test that series, game and goal records ignore unfinished series."""
    series = [
        make_series(1, 'A', 'B', 3, 1, goals=((1, 0), (0, 2), (3, 1), (1, 0))),
        make_series(2, 'A', 'B', 1, 0, finished=False, goals=((4, 0),)),
    ]
    records = tally(series, ['A', 'B'])
    a_record, b_record = records['A'], records['B']
    assert (a_record.match_wins, a_record.match_losses, a_record.game_wins, a_record.game_losses) == (1, 0, 3, 1)
    assert (a_record.round_wins, a_record.round_losses, a_record.rounds_known) == (5, 3, True)
    assert (b_record.match_wins, b_record.match_losses, b_record.round_wins, b_record.round_losses) == (0, 1, 3, 5)


def test_tally_flags_unknown_goals():
    """Test that a counted series without goals makes the goal record unknown."""
    records = tally([make_series(1, 'A', 'B', 3, 0)], ['A', 'B'])
    assert not records['A'].rounds_known


def test_tally_ignores_teams_outside_scope():
    """Test that only series between the given teams are counted."""
    records = tally([make_series(1, 'A', 'C', 3, 0)], ['A', 'B'])
    assert records['A'].match_wins == 0
    assert 'C' not in records


# === rank ===


def test_rank_by_match_score():
    """Test a plain ranking by series wins."""
    series = [make_series(1, 'A', 'B', 3, 0), make_series(1, 'B', 'C', 3, 0), make_series(1, 'A', 'C', 3, 0)]
    assert _ranking(rank(series, 'ABC', _tiebreakers('matchscore'))) == [(1, ('A',)), (2, ('B',)), (3, ('C',))]


def test_rank_full_game_diff_leaves_tie(three_way_tie):
    """Test that a tie surviving every tiebreaker is kept as a shared position."""
    groups = rank(three_way_tie, 'ABCD', _tiebreakers('matchscore', 'gamediff'))
    assert _ranking(groups) == [(1, ('A', 'B')), (3, ('C',)), (4, ('D',))]


def test_rank_mini_league_splits_three_way_tie(three_way_tie):
    """Test that ml. tiebreakers only count series between the tied teams."""
    groups = rank(three_way_tie, 'ABCD', _tiebreakers('matchscore', 'ml.gamediff'))
    assert _ranking(groups) == [(1, ('A',)), (2, ('B',)), (3, ('C',)), (4, ('D',))]


def test_rank_mini_league_uses_current_subgroup(three_way_tie):
    """Test that a subgroup left by a tiebreaker is resolved among its own members only.

    After gamediff, only A and B are tied: ml.gamediff counts A-B (3-0) alone, not the series against C.
    """
    groups = rank(three_way_tie, 'ABCD', _tiebreakers('matchscore', 'gamediff', 'ml.gamediff'))
    assert _ranking(groups) == [(1, ('A',)), (2, ('B',)), (3, ('C',)), (4, ('D',))]


def test_rank_goal_tiebreaker_with_known_goals(three_way_tie):
    """Test that goal difference settles a tie when every goal is known."""
    groups = rank(three_way_tie, 'ABCD', _tiebreakers('matchscore', 'gamediff', 'ml.rounddiff'))
    assert groups[0].teams == ('A',)


def test_rank_goal_tiebreaker_with_unknown_goals(three_way_tie):
    """Test that a goal tiebreaker cannot split a tie whose goals are unknown, nor let later tiebreakers do it."""
    without_goals = [make_series(s.round, s.team_1, s.team_2, s.score_1, s.score_2) for s in three_way_tie]
    tiebreakers = _tiebreakers('matchscore', 'gamediff', 'ml.rounddiff', 'manual')
    groups = rank(without_goals, 'ABCD', tiebreakers, {'B': 1})
    assert _ranking(groups) == [(1, ('A', 'B')), (3, ('C',)), (4, ('D',))]


def test_rank_manual_tiebreaker(three_way_tie):
    """Test that manual tiebreak points settle the remaining tie, higher first."""
    groups = rank(three_way_tie, 'ABCD', _tiebreakers('matchscore', 'gamediff', 'manual'), {'B': 1})
    assert _ranking(groups)[:2] == [(1, ('B',)), (2, ('A',))]


@pytest.mark.parametrize(
    ('tiebreaker', 'leader'),
    [('matchdiff', 'A'), ('matchwins', 'A'), ('gamescore', 'A'), ('gamewins', 'A'), ('roundwins', 'A')],
)
def test_rank_other_tiebreakers(tiebreaker, leader):
    """Test the remaining tiebreaker identifiers on a two-team league."""
    series = [make_series(1, 'A', 'B', 3, 1, goals=((1, 0), (0, 1), (2, 0), (1, 0)))]
    assert rank(series, 'AB', _tiebreakers(tiebreaker))[0].teams == (leader,)
