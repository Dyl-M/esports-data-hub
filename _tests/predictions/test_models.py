"""Tests for edh/predictions/models.py - played match validation."""

# Standard library
import datetime as dt

# Third-party
import pytest

# Local
from edh.predictions.models import Opponent, PlayedMatch, Player

PLAYED_AT = dt.datetime(2026, 5, 20, 15, tzinfo=dt.UTC)


def _match(score_1=3, score_2=1, *, name_2='Team B', played_at=PLAYED_AT):
    """A played match between Team A and name_2."""
    opponents = (Opponent('Team A', 'team a', score_1), Opponent(name_2, 'team b', score_2))
    return PlayedMatch('m1', 'Some/Page', played_at, '1', True, opponents)


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
