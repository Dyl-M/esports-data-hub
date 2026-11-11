"""Tests for edh/predictions/models.py - played match and lineup validation."""

# Third-party
import pytest

# Local
from edh.predictions.models import Lineup, Lineups, Opponent, Player

from .conftest import PLAYED_AT, make_match, make_opponent


def _match(score_1=3, score_2=1, *, name_2='Team B', played_at=PLAYED_AT):
    """A played match between Team A and name_2."""
    return make_match(make_opponent('Team A', score_1), make_opponent(name_2, score_2), played_at=played_at)


@pytest.mark.parametrize(('score_1', 'score_2', 'winner'), [(3, 1, 0), (2, 3, 1)])
def test_winner(score_1, score_2, winner):
    """Test that the winner is the opponent with more games."""
    assert _match(score_1, score_2).winner == winner


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'score_1': 2, 'score_2': 2}, 'has a winner'),
        ({'name_2': 'Team A'}, 'cannot play itself'),
        ({'played_at': PLAYED_AT.replace(tzinfo=None)}, 'timezone-aware'),
    ],
)
def test_invalid_match(kwargs, message):
    """Test that a tie, a team against itself or a naive time is rejected."""
    with pytest.raises(ValueError, match=message):
        _match(**kwargs)


def test_invalid_opponent_and_player():
    """Test that empty names and negative scores are rejected."""
    with pytest.raises(ValueError, match='opponent name'):
        Opponent('', '', 0)
    with pytest.raises(ValueError, match='score'):
        Opponent('Team A', '', -1)
    with pytest.raises(ValueError, match='player page'):
        Player('', 'Name')


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'kind': 'pro'}, 'kind must be'),
        ({'predecessor': 2}, 'earlier lineup'),
        ({'predecessor': -1}, 'earlier lineup'),
    ],
)
def test_invalid_lineup(kwargs, message):
    """Test that an unknown kind or a predecessor that is not an earlier lineup is rejected."""
    with pytest.raises(ValueError, match=message):
        Lineup(**{'lineup_id': 2, 'name': 'Team', 'kind': 'club', 'first_played': PLAYED_AT} | kwargs)


def test_lineups_of_a_match():
    """Test that the lineups of a match come in the order of its opponents."""
    lineups = (Lineup(0, 'Team A', 'club', PLAYED_AT), Lineup(1, 'Team B', 'club', PLAYED_AT, 0))
    assert Lineups(lineups, {'m1': (1, 0)}).of(_match()) == (lineups[1], lineups[0])
