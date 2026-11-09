"""Tests for edh/scenarios/engine.py - scenario counting, enumeration and sampling."""

# Third-party
import pytest

# Local
from edh.exceptions import ScenarioError
from edh.scenarios import engine
from edh.scenarios.engine import choose_method, count_scenarios, evaluate, project_ranks
from edh.scenarios.models import EvaluationOptions, QualificationSlot, RankGroup, TeamOutlook
from edh.scenarios.sources import CsvSource

from ..conftest import CDF_DAY2_CSV, make_league, make_series


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
    ('method', 'scenarios', 'combinations', 'expected'),
    [
        ('auto', 10, 4, 'exhaustive'),
        ('auto', 11, 4, 'winnersfirst'),
        ('auto', 11, 5, 'montecarlo'),
        ('auto', 11, None, 'montecarlo'),
        ('montecarlo', 1, 1, 'montecarlo'),
        ('exhaustive', 10, None, 'exhaustive'),
        ('winnersfirst', 1, 4, 'winnersfirst'),
    ],
)
def test_choose_method(method, scenarios, combinations, expected):
    """Test that auto enumerates scenarios, then winners, up to their limits, and samples above them."""
    assert choose_method(method, scenarios, combinations, max_exhaustive=10, max_combinations=4) == expected


def test_choose_method_uses_module_limits(monkeypatch):
    """Test that the default limits are read at call time."""
    monkeypatch.setattr(engine, 'MAX_EXHAUSTIVE_SCENARIOS', 5)
    monkeypatch.setattr(engine, 'MAX_WINNER_COMBINATIONS', 2)
    assert choose_method('auto', 6, 2) == 'winnersfirst'
    assert choose_method('auto', 6, 3) == 'montecarlo'


@pytest.mark.parametrize(
    ('method', 'combinations', 'message'),
    [
        ('exhaustive', 1, 'exhaustive limit'),
        ('winnersfirst', None, 'first tiebreaker on series winners'),
        ('winnersfirst', 5, 'winners-first limit'),
        ('random', 1, 'Unknown method'),
    ],
)
def test_choose_method_errors(method, combinations, message):
    """Test that unknown methods and exact methods out of reach are rejected."""
    with pytest.raises(ScenarioError, match=message):
        choose_method(method, 11, combinations, max_exhaustive=10, max_combinations=4)


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
    first = evaluate(make_league(), one_series_left, EvaluationOptions(method='montecarlo', samples=3000, seed=7))
    second = evaluate(make_league(), one_series_left, EvaluationOptions(method='montecarlo', samples=3000, seed=7))
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
        evaluate(
            make_league(),
            series if series is not None else [make_series(1, 'A', 'B')],
            EvaluationOptions(samples=samples),
        )


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
    report = evaluate(make_league(), one_series_left, EvaluationOptions(method='montecarlo', samples=25, seed=1))
    assert report.scenarios == 25
    assert all(sum(item.rank_shares) == pytest.approx(1) for item in report.teams)


def test_evaluate_monte_carlo_without_pending_series():
    """Test that sampling a finished league repeats its only scenario."""
    report = evaluate(
        make_league(), [make_series(1, 'A', 'B', 3, 0)], EvaluationOptions(method='montecarlo', samples=5, seed=1)
    )
    assert (report.scenarios, report.remaining) == (5, 0)
    assert report.teams[0].rank_shares == (1.0, 0.0)


def test_evaluate_auto_falls_back_to_winners_first(one_series_left, monkeypatch):
    """Test that auto switches to the winners-first method above the exhaustive limit, with the same shares."""
    exhaustive = evaluate(make_league(qualification={'Top': (1, 1)}), one_series_left)
    monkeypatch.setattr(engine, 'MAX_EXHAUSTIVE_SCENARIOS', 5)
    report = evaluate(make_league(qualification={'Top': (1, 1)}), one_series_left)
    assert (report.method, report.scenarios, report.exact) == ('winnersfirst', 6, True)
    assert [item.team for item in report.teams] == [item.team for item in exhaustive.teams]
    for item, expected in zip(report.teams, exhaustive.teams, strict=True):
        assert item.rank_shares == pytest.approx(expected.rank_shares)
        assert item.qualified == pytest.approx(expected.qualified)
        assert item.undecided == pytest.approx(expected.undecided)
        assert item.unresolved == pytest.approx(expected.unresolved)


def test_evaluate_auto_skips_winners_first_on_margin_tiebreaker(one_series_left, monkeypatch):
    """Test that auto samples when the first tiebreaker needs game margins."""
    monkeypatch.setattr(engine, 'MAX_EXHAUSTIVE_SCENARIOS', 5)
    report = evaluate(make_league(('gamediff', 'matchscore')), one_series_left, EvaluationOptions(samples=10, seed=1))
    assert report.method == 'montecarlo'


@pytest.mark.parametrize(('method', 'samples'), [('exhaustive', 1), ('winnersfirst', 1), ('montecarlo', 25)])
def test_evaluate_in_worker_processes_matches_in_process(one_series_left, monkeypatch, method, samples):
    """Test that a run split over worker processes gives exactly the in-process result (chunks merged in order)."""
    monkeypatch.setattr(engine, 'PARALLEL_MIN_WORK', 0)
    monkeypatch.setattr(engine, 'SAMPLE_BATCH', 10)
    league = make_league(qualification={'Top': (1, 1)})
    single = evaluate(league, one_series_left, EvaluationOptions(method=method, samples=samples, seed=3, workers=1))
    pooled = evaluate(league, one_series_left, EvaluationOptions(method=method, samples=samples, seed=3, workers=2))
    assert pooled == single
    assert single.scenarios == (25 if method == 'montecarlo' else 6)


def test_prefix_chunks_cover_every_scenario(monkeypatch):
    """Test that exact runs are cut by the outcomes of the first series until there are enough chunks."""
    monkeypatch.setattr(engine, 'PARALLEL_CHUNKS', 7)
    series = [make_series(1, 'A', 'B'), make_series(1, 'C', 'D'), make_series(2, 'A', 'C')]
    league = make_league()
    job = engine._Job(league, ('A', 'B', 'C', 'D'), (), tuple(series), {}, 'exhaustive')
    assert len(engine._prefix_chunks(job)) == 36  # 6 < 7, then 6 x 6
    winners = engine._Job(league, ('A', 'B', 'C', 'D'), (), tuple(series), {}, 'winnersfirst')
    assert len(engine._prefix_chunks(winners)) == 8  # 2, 4 < 7, then 2 x 2 x 2


@pytest.mark.parametrize(('target', 'chunks'), [(1, 1), (6, 6), (7, 36)])
def test_prefix_chunks_stop_at_the_target(monkeypatch, target, chunks):
    """Test that exact runs get no more chunks than needed to reach PARALLEL_CHUNKS (one series fixes 6 outcomes)."""
    monkeypatch.setattr(engine, 'PARALLEL_CHUNKS', target)
    series = (make_series(1, 'A', 'B'), make_series(1, 'C', 'D'))
    job = engine._Job(make_league(), ('A', 'B', 'C', 'D'), (), series, {}, 'exhaustive')
    assert len(engine._prefix_chunks(job)) == chunks


def test_sample_chunks_draw_exactly_the_requested_samples(monkeypatch):
    """Test that Monte Carlo chunks hold full batches then the remainder, adding up to the requested sample count."""
    monkeypatch.setattr(engine, 'SAMPLE_BATCH', 10)
    job = engine._Job(make_league(), ('A', 'B'), (), (make_series(1, 'A', 'B'),), {}, 'montecarlo')
    assert [chunk.samples for chunk in engine._sample_chunks(job, 25, 1)] == [10, 10, 5]


def _job(method, *, scenarios=0, combinations=None):
    """An exact job on two pending series (36 scenarios, 4 winner combinations) with the given work counts."""
    series = (make_series(1, 'A', 'B'), make_series(1, 'C', 'D'))
    return engine._Job(make_league(), ('A', 'B', 'C', 'D'), (), series, {}, method, scenarios, combinations)


@pytest.mark.parametrize(
    ('job', 'workers', 'expected'),
    [
        pytest.param(_job('exhaustive', scenarios=99), 3, 1, id='exhaustive-small'),
        pytest.param(_job('exhaustive', scenarios=100), 3, 3, id='exhaustive-at-threshold'),
        pytest.param(_job('exhaustive', scenarios=100), None, 8, id='exhaustive-all-cores'),
        pytest.param(_job('winnersfirst', combinations=4), 3, 1, id='winners-small'),
        pytest.param(_job('winnersfirst', combinations=5), 3, 3, id='winners-weighted'),
    ],
)
def test_plan_worker_count(monkeypatch, job, workers, expected):
    """Test that small work stays in-process and larger work uses the workers asked for, or all cores.

    A winner combination weighs PARALLEL_WINNERS_COST scenarios against PARALLEL_MIN_WORK.
    """
    monkeypatch.setattr(engine, 'PARALLEL_MIN_WORK', 100)
    monkeypatch.setattr(engine, 'PARALLEL_WINNERS_COST', 20)
    monkeypatch.setattr(engine.os, 'cpu_count', lambda: 8)
    chunks, count = engine._plan(job, EvaluationOptions(workers=workers))
    assert len(chunks) > 1
    assert count == expected


def test_plan_single_chunk_or_unknown_cores(monkeypatch):
    """Test that a single chunk stays in-process, and that an unknown core count means one worker."""
    monkeypatch.setattr(engine, 'PARALLEL_MIN_WORK', 0)
    single = engine._Job(make_league(), ('A', 'B'), (), (), {}, 'exhaustive', 1)
    assert engine._plan(single, EvaluationOptions(workers=3))[1] == 1
    monkeypatch.setattr(engine.os, 'cpu_count', lambda: None)
    assert engine._plan(_job('exhaustive', scenarios=36), EvaluationOptions())[1] == 1


def test_tie_straddling_the_start_of_a_slot_is_undecided():
    """Test that teams tied over ranks 2-3 are undecided for a slot starting at rank 3, not qualified or out."""
    totals = engine._Accumulator(('A', 'B', 'C', 'D'), [QualificationSlot('Play-in', 3, 4)])
    totals.add([RankGroup(1, ('A',)), RankGroup(2, ('B', 'C')), RankGroup(4, ('D',))])
    assert {team: totals.undecided[team]['Play-in'] for team in 'ABCD'} == {'A': 0, 'B': 1, 'C': 1, 'D': 0}
    assert {team: totals.qualified[team]['Play-in'] for team in 'ABCD'} == {'A': 0, 'B': 0, 'C': 0, 'D': 1}
