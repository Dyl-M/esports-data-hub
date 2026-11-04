"""Tests for edh/scenarios/winners.py - winners-first exact evaluation."""

# Standard library
from dataclasses import replace
from itertools import combinations

# Third-party
import numpy as np
import pytest

# Local
from edh.exceptions import ScenarioError
from edh.scenarios import winners
from edh.scenarios.engine import evaluate
from edh.scenarios.models import EvaluationOptions, Tiebreaker
from edh.scenarios.sources import CsvSource
from edh.scenarios.winners import WinnersFirst, count_winner_combinations, margin_free_prefix

from .conftest import CDF_DAY2_CSV, make_league, make_series

# Tiebreaker chains covering every game statistic kept (difference, games won, both, none), `ml.` before and after
# full-league tiebreakers, manual points and unresolved goal tiebreakers
CHAINS = [
    ('matchscore', 'gamediff', 'ml.gamediff', 'ml.rounddiff', 'manual'),
    ('matchscore', 'ml.matchscore', 'gamediff', 'gamewins'),
    ('matchwins', 'gamescore', 'ml.gamescore'),
    ('matchscore', 'ml.gamewins', 'gamediff', 'rounddiff'),
    ('matchdiff', 'manual', 'ml.gamediff', 'gamediff'),
    ('matchscore',),
]


def assert_same_outlook(report, expected):
    """Assert that two reports give the same shares, in the same projected order."""
    assert [item.team for item in report.teams] == [item.team for item in expected.teams]
    for item, other in zip(report.teams, expected.teams, strict=True):
        assert item.rank_shares == pytest.approx(other.rank_shares, abs=1e-12)
        assert item.qualified == pytest.approx(other.qualified, abs=1e-12)
        assert item.undecided == pytest.approx(other.undecided, abs=1e-12)
        assert item.unresolved == pytest.approx(other.unresolved, abs=1e-12)


def random_league(rng, chain):
    """Build a small random round robin: played series (goals mostly known) and a few pending ones."""
    teams = [chr(ord('A') + index) for index in range(int(rng.integers(4, 7)))]
    pairs = list(combinations(teams, 2))
    pairs = [pairs[index] for index in rng.permutation(len(pairs))]
    best_of = 3 if rng.random() < 0.5 else 5
    needed = best_of // 2 + 1
    left = int(rng.integers(1, 6))
    starts = [(0, 0), (0, 0), (1, 0), (0, 1), (1, 1)]
    series = []
    for position, (team_1, team_2) in enumerate(pairs):
        if position < len(pairs) - left:
            lost = int(rng.integers(needed))
            score = (needed, lost) if rng.random() < 0.5 else (lost, needed)
            goals = tuple((1, 0) if game < score[0] else (0, 2) for game in range(sum(score)))
            series.append(make_series(1, team_1, team_2, *score, goals=goals if rng.random() < 0.8 else None))
        else:
            current = starts[int(rng.integers(len(starts)))]
            series.append(make_series(2, team_1, team_2, *current, finished=False))
    league = make_league(
        chain,
        best_of=best_of,
        qualification={'Top': (1, 2), 'Mid': (3, 4)},
        manual_tiebreakers={team: int(rng.integers(3)) for team in teams},
    )
    return league, series


@pytest.mark.parametrize(
    ('tiebreakers', 'expected'),
    [
        (('matchscore', 'gamediff'), 1),
        (('matchscore', 'ml.matchscore', 'manual', 'gamediff', 'matchwins'), 3),
        (('gamediff', 'matchscore'), 0),
        ((), 0),
    ],
)
def test_margin_free_prefix(tiebreakers, expected):
    """Test that only the leading series-winner and manual tiebreakers count."""
    assert margin_free_prefix([Tiebreaker.parse(item) for item in tiebreakers]) == expected


def test_count_winner_combinations_cdf_snapshot():
    """Test the winner combination count on the CDF snapshot: 23 unfinished series, 2 possible winners each."""
    series = CsvSource(CDF_DAY2_CSV).fetch(make_league())
    assert count_winner_combinations(series, 3) == 2**23


def test_count_winner_combinations_decided_series():
    """Test that a series already won (but not flagged finished) has a single winner."""
    assert count_winner_combinations([make_series(1, 'A', 'B', 3, 1, finished=False)], 3) == 1


def test_winner_probability_follows_reachable_scores():
    """Test that a Bo5 at 2-0 is won by the leader in 3 of its 4 reachable scores."""
    report = evaluate(
        make_league(), [make_series(1, 'A', 'B', 2, 0, finished=False)], EvaluationOptions(method='winnersfirst')
    )
    outlook = {item.team: item for item in report.teams}
    assert outlook['A'].rank_shares == pytest.approx((0.75, 0.25))
    assert report.scenarios == 4


def test_combination_probabilities_sum_to_one():
    """Test that winner combinations are weighted by their probability, and that every team is ranked in each."""
    series = [make_series(1, 'A', 'B', 1, 0, finished=False), make_series(1, 'B', 'C'), make_series(1, 'A', 'C')]
    evaluator = WinnersFirst([], series, make_league(), {})
    combinations_ = list(evaluator.combinations())
    assert len(combinations_) == 8
    assert sum(probability for probability, _ in combinations_) == pytest.approx(1)
    for probability, groups in combinations_:
        weights = {team: 0.0 for team in 'ABC'}
        for group, weight in groups:
            for team in group.teams:
                weights[team] += weight
        assert weights == pytest.approx(dict.fromkeys('ABC', probability))


def test_requires_series_winner_tiebreaker():
    """Test that a first tiebreaker on game margins is rejected."""
    with pytest.raises(ScenarioError, match='first tiebreaker on series winners'):
        WinnersFirst([], [make_series(1, 'A', 'B')], make_league(('gamediff', 'matchscore')), {})


def test_matches_exhaustive_on_cdf_snapshot():
    """Test that winners-first gives the exhaustive shares on the CDF snapshot cut to 5 series left."""
    league = make_league(
        ('matchscore', 'gamediff', 'ml.gamediff', 'ml.rounddiff', 'manual'), qualification={'Playoffs': (1, 4)}
    )
    series = CsvSource(CDF_DAY2_CSV).fetch(league)
    pending = [item for item in series if not item.finished]
    played = [replace(item, score_1=3, score_2=index % 3, finished=True) for index, item in enumerate(pending[:-5])]
    league_series = [*(item for item in series if item.finished), *played, *pending[-5:]]
    expected = evaluate(league, league_series, EvaluationOptions(method='exhaustive'))
    report = evaluate(league, league_series, EvaluationOptions(method='winnersfirst'))
    assert (report.method, report.scenarios) == ('winnersfirst', expected.scenarios)
    assert_same_outlook(report, expected)


@pytest.mark.parametrize(('seed', 'chain'), list(enumerate(CHAINS)))
def test_matches_exhaustive_on_random_leagues(seed, chain):
    """Test that winners-first gives the exhaustive shares on random leagues, for each tiebreaker chain."""
    rng = np.random.default_rng(seed)
    for _ in range(8):
        league, series = random_league(rng, chain)
        assert_same_outlook(
            evaluate(league, series, EvaluationOptions(method='winnersfirst')),
            evaluate(league, series, EvaluationOptions(method='exhaustive')),
        )


def test_matches_exhaustive_without_coded_patterns(monkeypatch):
    """Test the column-wise comparison of patterns used when they are too long to code as integers."""
    monkeypatch.setattr(winners, '_MAX_CODED_ROWS', 0)
    rng = np.random.default_rng(7)
    for chain in CHAINS[:3]:
        league, series = random_league(rng, chain)
        assert_same_outlook(
            evaluate(league, series, EvaluationOptions(method='winnersfirst')),
            evaluate(league, series, EvaluationOptions(method='exhaustive')),
        )
