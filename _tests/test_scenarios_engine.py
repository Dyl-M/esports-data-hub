"""Tests for edh/scenarios/engine.py - scenario counting, enumeration and sampling."""

# Third-party
import pytest

# Local
from edh.exceptions import ScenarioError
from edh.scenarios import engine
from edh.scenarios.engine import choose_method, count_scenarios, evaluate, project_ranks
from edh.scenarios.models import TeamOutlook
from edh.scenarios.sources import CsvSource

from .conftest import CDF_DAY2_CSV, make_league, make_series


@pytest.fixture
def one_series_left():
    """A beat B 3-0, B beat C 3-0, C vs A to play: 6 equally likely scenarios.

    A wins (3 scenarios): A, B, C. C wins 3-1 or 3-2: A, B, C on game diff. C wins 3-0: three-way tie.
    """
    return [make_series(1, 'A', 'B', 3, 0), make_series(2, 'B', 'C', 3, 0), make_series(3, 'C', 'A')]


def test_count_scenarios_cdf_snapshot():
    """Test the scenario count on the CDF snapshot: one series at 1-1 (4 scores) and 22 upcoming (6 each)."""
    series = CsvSource(CDF_DAY2_CSV).fetch(make_league())
    assert count_scenarios(series, 3) == 4 * 6**22


def test_count_scenarios_all_finished():
    """Test that a finished league has a single scenario."""
    assert count_scenarios([make_series(1, 'A', 'B', 3, 0)], 3) == 1


@pytest.mark.parametrize(
    ('method', 'scenarios', 'expected'),
    [
        ('auto', 10, 'exhaustive'),
        ('auto', 11, 'montecarlo'),
        ('montecarlo', 1, 'montecarlo'),
        ('exhaustive', 10, 'exhaustive'),
    ],
)
def test_choose_method(method, scenarios, expected):
    """Test that auto enumerates up to the limit and samples above it."""
    assert choose_method(method, scenarios, 10) == expected


def test_choose_method_uses_module_limit(monkeypatch):
    """Test that the default limit is read at call time."""
    monkeypatch.setattr(engine, 'MAX_EXHAUSTIVE_SCENARIOS', 5)
    assert choose_method('auto', 6) == 'montecarlo'


@pytest.mark.parametrize(('method', 'message'), [('exhaustive', 'exhaustive limit'), ('random', 'Unknown method')])
def test_choose_method_errors(method, message):
    """Test that unknown methods and oversized enumerations are rejected."""
    with pytest.raises(ScenarioError, match=message):
        choose_method(method, 11, 10)


def test_evaluate_exhaustive_shares(one_series_left):
    """Test exact rank, qualification and tie shares."""
    league = make_league(qualification={'Top': (1, 1)})
    report = evaluate(league, one_series_left)
    assert (report.method, report.scenarios, report.remaining, report.exact) == ('exhaustive', 6, 1, True)
    outlook = {item.team: item for item in report.teams}
    assert outlook['A'].rank_shares == pytest.approx((5 / 6 + 1 / 18, 1 / 18, 1 / 18))
    assert outlook['A'].qualified['Top'] == pytest.approx(5 / 6)
    assert outlook['A'].undecided['Top'] == pytest.approx(1 / 6)
    assert outlook['C'].qualified['Top'] == 0
    assert all(item.unresolved == pytest.approx(1 / 6) for item in report.teams)
    assert [item.team for item in report.teams] == ['A', 'B', 'C']


def test_evaluate_rank_shares_sum_to_one(one_series_left):
    """Test that each team's rank shares and each rank's team shares sum to 1."""
    report = evaluate(make_league(), one_series_left)
    for item in report.teams:
        assert sum(item.rank_shares) == pytest.approx(1)
    for position in range(3):
        assert sum(item.rank_shares[position] for item in report.teams) == pytest.approx(1)


def test_evaluate_monte_carlo_is_seeded(one_series_left):
    """Test that a seeded Monte Carlo run is reproducible and close to the exact shares."""
    first = evaluate(make_league(), one_series_left, method='montecarlo', samples=3000, seed=7)
    second = evaluate(make_league(), one_series_left, method='montecarlo', samples=3000, seed=7)
    assert first == second
    assert (first.method, first.scenarios, first.exact) == ('montecarlo', 3000, False)
    outlook = {item.team: item for item in first.teams}
    assert outlook['A'].rank_shares[0] == pytest.approx(5 / 6 + 1 / 18, abs=0.03)


def test_evaluate_applies_aliases():
    """Test that renamed teams are merged before ranking."""
    series = [make_series(1, 'Old', 'B', 3, 0), make_series(2, 'New', 'B', 3, 0)]
    report = evaluate(make_league(team_aliases={'Old': 'New'}), series)
    assert {item.team for item in report.teams} == {'New', 'B'}


def test_evaluate_in_progress_series_is_conditioned():
    """Test that a series at 2-0 is only completed with the scores still reachable."""
    report = evaluate(make_league(), [make_series(1, 'A', 'B', 2, 0, finished=False)])
    assert report.scenarios == 4
    assert report.teams[0].team == 'A'


@pytest.mark.parametrize(('series', 'samples', 'message'), [([], 10, 'No series'), (None, 0, 'samples')])
def test_evaluate_errors(series, samples, message):
    """Test that empty leagues and invalid sample counts are rejected."""
    with pytest.raises(ScenarioError, match=message):
        evaluate(make_league(), series if series is not None else [make_series(1, 'A', 'B')], samples=samples)


def test_evaluate_aliases_manual_tiebreakers():
    """Test that manual tiebreak points given under an old team name follow the rename."""
    series = [make_series(1, 'Old', 'B', 3, 2), make_series(2, 'B', 'Old', 3, 2)]
    league = make_league(
        ('matchscore', 'gamediff', 'manual'), team_aliases={'Old': 'New'}, manual_tiebreakers={'Old': 1}
    )
    report = evaluate(league, series)
    assert report.teams[0].team == 'New'
    assert report.teams[0].unresolved == 0


def _outlook(team, *shares):
    """Build an outlook from rank shares only."""
    return TeamOutlook(team, shares, {}, {}, 0.0)


def test_project_ranks_resolves_collisions():
    """Test that the likelier team keeps a contested rank and the other falls back to its next likeliest free one.

    Both A and B are most likely 1st; A's 50% beats B's 30%, so B takes 2nd. C's 3rd is then the only rank left.
    """
    outlooks = [_outlook('A', 0.5, 0.3, 0.2), _outlook('B', 0.3, 0.29, 0.41), _outlook('C', 0.2, 0.41, 0.39)]
    assert project_ranks(outlooks) == {'A': 1, 'C': 2, 'B': 3}


def test_project_ranks_equal_shares():
    """Test that equal shares go to the better rank, then to the better expected rank."""
    assert project_ranks([_outlook('A', 0.5, 0.5), _outlook('B', 0.5, 0.5)]) == {'A': 1, 'B': 2}
    assert project_ranks([_outlook('A', 0.4, 0.6), _outlook('B', 0.6, 0.4)]) == {'B': 1, 'A': 2}


def test_evaluate_orders_teams_by_projected_rank(one_series_left):
    """Test that every team gets a distinct projected rank and the report follows it."""
    report = evaluate(make_league(), one_series_left)
    assert [(item.team, item.projected_rank) for item in report.teams] == [('A', 1), ('B', 2), ('C', 3)]


def test_evaluate_monte_carlo_batches(one_series_left, monkeypatch):
    """Test that sampling in batches yields exactly the requested sample count, including a partial last batch."""
    monkeypatch.setattr(engine, 'SAMPLE_BATCH', 10)
    report = evaluate(make_league(), one_series_left, method='montecarlo', samples=25, seed=1)
    assert report.scenarios == 25
    assert all(sum(item.rank_shares) == pytest.approx(1) for item in report.teams)


def test_evaluate_monte_carlo_without_pending_series():
    """Test that sampling a finished league repeats its only scenario."""
    report = evaluate(make_league(), [make_series(1, 'A', 'B', 3, 0)], method='montecarlo', samples=5, seed=1)
    assert (report.scenarios, report.remaining) == (5, 0)
    assert report.teams[0].rank_shares == (1.0, 0.0)
