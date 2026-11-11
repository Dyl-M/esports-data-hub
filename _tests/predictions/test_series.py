"""Tests for edh/predictions/series.py - per-game probability to series outcomes."""

# Third-party
import pytest

# Local
from edh.predictions.series import score_distribution, series_probability


def test_fair_best_of_five():
    """Test the fair-game Bo5: 3-0 has probability 1/8, 3-1 and 3-2 have 3/16 each, per winner."""
    assert score_distribution(0.5, 3) == pytest.approx(
        {(3, 0): 1 / 8, (3, 1): 3 / 16, (3, 2): 3 / 16, (0, 3): 1 / 8, (1, 3): 3 / 16, (2, 3): 3 / 16}
    )


@pytest.mark.parametrize(('p', 'wins_needed', 'expected'), [(0.5, 3, 0.5), (0.6, 3, 0.68256), (0.6, 4, 0.710208)])
def test_series_probability(p, wins_needed, expected):
    """Test the series win probability, e.g. 0.6 per game gives 0.68256 in a Bo5."""
    assert series_probability(p, wins_needed) == pytest.approx(expected)


def test_series_in_progress():
    """Test that a series continues from its current score: at 2-0 in a fair Bo5 the leader wins 7/8."""
    scores = score_distribution(0.5, 3, (2, 0))
    assert scores == pytest.approx({(3, 0): 1 / 2, (3, 1): 1 / 4, (3, 2): 1 / 8, (2, 3): 1 / 8})


def test_series_in_progress_both_scored():
    """Test a series where both teams already won games: at 1-2 in a Bo5, p = 0.6 per game for the first team."""
    scores = score_distribution(0.6, 3, (1, 2))
    assert scores == pytest.approx({(3, 2): 0.36, (1, 3): 0.4, (2, 3): 0.24})


@pytest.mark.parametrize('p', [0.0, 0.3, 1.0])
def test_distribution_sums_to_one(p):
    """Test that the final scores of a series always sum to 1, certain games included."""
    assert sum(score_distribution(p, 4, (1, 2)).values()) == pytest.approx(1)


@pytest.mark.parametrize(
    ('p', 'current', 'message'),
    [(1.2, (0, 0), 'between 0 and 1'), (0.5, (3, 0), 'below 3'), (0.5, (0, 3), 'below 3'), (0.5, (0, -1), 'below')],
)
def test_invalid_input(p, current, message):
    """Test that a probability outside [0, 1] or a finished / impossible current score is rejected."""
    with pytest.raises(ValueError, match=message):
        score_distribution(p, 3, current)
