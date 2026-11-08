"""Tests for edh/scenarios/outcomes.py - possible final scores of a series."""

# Third-party
import pytest

# Local
from edh.exceptions import ScenarioError
from edh.scenarios.outcomes import reachable_scores, series_scores

from ..conftest import make_series


@pytest.mark.parametrize(
    ('wins_needed', 'expected'),
    [
        (1, ((1, 0), (0, 1))),
        (2, ((2, 0), (2, 1), (0, 2), (1, 2))),
        (3, ((3, 0), (3, 1), (3, 2), (0, 3), (1, 3), (2, 3))),
    ],
)
def test_series_scores(wins_needed, expected):
    """Test that a best-of-(2k-1) has 2k final scores, wins first."""
    assert series_scores(wins_needed) == expected


def test_series_scores_rejects_zero():
    """Test that a series must need at least one win."""
    with pytest.raises(ValueError, match='wins_needed'):
        series_scores(0)


def test_reachable_scores_upcoming_series():
    """Test that an upcoming series can end with any score."""
    assert reachable_scores(make_series(1, 'A', 'B'), 3) == series_scores(3)


def test_reachable_scores_in_progress_series():
    """Test that a series at 1-1 can no longer end 3-0 or 0-3."""
    series = make_series(1, 'A', 'B', 1, 1, finished=False)
    assert reachable_scores(series, 3) == ((3, 1), (3, 2), (1, 3), (2, 3))


def test_reachable_scores_finished_series():
    """Test that a finished series keeps its actual score."""
    assert reachable_scores(make_series(1, 'A', 'B', 3, 1), 3) == ((3, 1),)


def test_reachable_scores_impossible_score():
    """Test that an unfinished series past the win threshold is rejected."""
    with pytest.raises(ScenarioError, match='impossible'):
        reachable_scores(make_series(1, 'A', 'B', 4, 0, finished=False), 3)


@pytest.mark.parametrize(('score_1', 'score_2'), [(3, 1), (0, 3)])
def test_reachable_scores_won_but_not_flagged(score_1, score_2):
    """Test that a series already at the winning score keeps it, even if not flagged as finished."""
    series = make_series(1, 'A', 'B', score_1, score_2, finished=False)
    assert reachable_scores(series, 3) == ((score_1, score_2),)
