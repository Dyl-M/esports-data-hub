"""Tests for edh/predictions/tuning.py - grid, bounded search and parallel tuning."""

# Standard library
import datetime as dt
import math
from dataclasses import replace
from functools import partial

# Third-party
import numpy as np
import pytest

# Local
from edh.exceptions import DataSourceError
from edh.predictions import tuning
from edh.predictions.constants import LOG_SCALE
from edh.predictions.lineups import identify_lineups
from edh.predictions.ratings import Elo, Uniform
from edh.predictions.series import series_probability
from edh.predictions.tuning import MODELS, SearchSpace, evaluate_all, tune

from .conftest import make_match, make_opponent

START = dt.datetime(2025, 5, 1, 18, tzinfo=dt.UTC)  # RLCS 2025 until day 135 (September 13), then RLCS 2026


class _Fixed(Uniform):
    """A model giving the first team the same game probability `p` in every match."""

    def __init__(self, p):
        self.p = p

    def game_probability(self, first, second):
        """Return `p`, whoever plays."""
        return self.p


def _history(days, first_loses=()):
    """Team A against Team B on these days (from START), A winning 3-1 except on the days in `first_loses`."""
    return [
        make_match(
            make_opponent('Team A', 1 if day in first_loses else 3, 'a1 a2 a3'),
            make_opponent('Team B', 3 if day in first_loses else 1, 'b1 b2 b3'),
            match_id=f'm{day:03d}',
            played_at=START + dt.timedelta(days=day),
        )
        for day in days
    ]


def _tune(monkeypatch, space, matches):
    """Tune a model with this search space on a history, by name."""
    monkeypatch.setitem(MODELS, 'test', space)
    return tune(matches, identify_lineups(matches), 'test', 'name')


# === Grid ===


def test_grid_keeps_the_best_tuning_log_loss(monkeypatch):
    """Test that without bounds, tuning keeps the grid point with the lowest tuning log loss, scored on 2026."""
    matches = _history(range(0, 200, 10))  # A always wins: learning helps
    result = _tune(monkeypatch, SearchSpace(Elo, {'k': (0, 60)}), matches)
    assert (result.model, result.identity, result.params, result.at_bound) == ('test', 'name', {'k': 60}, ())
    assert [trial.params for trial in result.trials] == [{'k': 0}, {'k': 60}]
    assert result.trials[0].log_loss == pytest.approx(math.log(2))
    assert result.tuning.matches == len([day for day in range(0, 200, 10) if day < 135])
    assert {prediction.season for prediction in result.predictions} == {2026}


def test_ties_keep_the_first_run(monkeypatch):
    """Test that among runs with the same tuning log loss, the first one is kept."""
    result = _tune(monkeypatch, SearchSpace(lambda k: Uniform(), {'k': (2, 1)}), _history([0, 200]))
    assert result.params == {'k': 2}


# === Bounded search ===


def test_search_finds_the_best_point_off_the_grid(monkeypatch):
    """Test that the search moves past the grid to the point where the series probability matches the share won."""
    matches = _history(range(0, 200, 5), first_loses=range(0, 200, 20))  # A wins 3 series of 4
    result = _tune(monkeypatch, SearchSpace(_Fixed, {'p': (0.5, 0.9)}, {'p': (0.05, 0.95)}), matches)
    grid_best = min(trial.log_loss for trial in result.trials[:2])
    assert result.params['p'] not in (0.5, 0.9)
    assert result.tuning.log_loss < grid_best
    assert series_probability(result.params['p'], 3) == pytest.approx(0.75, abs=0.01)
    assert result.at_bound == ()


def test_search_stops_at_a_bound(monkeypatch):
    """Test that a pick on a bound is reported, from a start near it (the first step goes inward)."""
    result = _tune(monkeypatch, SearchSpace(_Fixed, {'p': (0.5, 0.9)}, {'p': (0.05, 0.95)}), _history([0, 200]))
    assert result.params == {'p': 0.95}
    assert result.at_bound == ('p',)


def test_search_runs_each_point_once(monkeypatch):
    """Test that hyperparameters equal once rounded to `TUNING_DIGITS` are one run."""
    matches = _history(range(0, 200, 5), first_loses=range(0, 200, 20))
    result = _tune(monkeypatch, SearchSpace(_Fixed, {'p': (0.5, 0.9)}, {'p': (0.05, 0.95)}), matches)
    tried = [trial.params['p'] for trial in result.trials]
    assert len(tried) == len(set(tried))
    assert all(float(f'{p:.4g}') == p for p in tried)


@pytest.mark.parametrize(
    ('params', 'at_bound'),
    [
        ({'shrink': 0.0, 'offset': -50}, ('shrink',)),
        ({'shrink': 0.5, 'offset': -399}, ('offset',)),  # within 1% of the range from -400
        ({'shrink': 0.5, 'offset': -394}, ()),
        ({'shrink': 0.995, 'offset': 100}, ('shrink', 'offset')),
        ({'shrink': 0.01, 'offset': -395}, ('shrink', 'offset')),  # exactly 1% from a bound: on it
        ({'shrink': 0.99, 'offset': 95}, ('shrink', 'offset')),
    ],
)
def test_near_a_bound_counts_as_on_it(params, at_bound):
    """Test that a pick within `TUNING_TOLERANCE` of a bound (a share of its scaled range) is reported on it."""
    assert tuning._at_bound(params, {'shrink': (0, 1), 'offset': (-400, 100)}) == at_bound


@pytest.mark.parametrize(
    ('name', 'bounds', 'value', 'unit'),
    [('l2', (0.01, 100), 1, 0.5), ('shrink', (0, 1), 0.25, 0.25), ('offset', (-400, 100), -150, 0.5)],
)
def test_unit_scale(name, bounds, value, unit):
    """Test that a value is placed between its bounds on a log scale for `LOG_SCALE`, linearly otherwise."""
    assert tuning._to_unit(name, value, bounds) == pytest.approx(unit)
    assert tuning._from_unit(name, unit, bounds) == value


def test_from_unit_rounds_and_stays_in_bounds():
    """Test that a value is rounded to 4 significant digits and never leaves its bounds."""
    assert tuning._from_unit('k', 1.0, (4, 128)) == 128
    assert tuning._from_unit('k', 0.0, (4, 128)) == 4
    assert tuning._from_unit('shrink', 1 / 3, (0, 1)) == 0.3333


def test_first_simplex_steps_inside_the_cube():
    """Test that the first simplex steps 0.1 up each axis, down when going up would leave the unit cube."""
    simplex = tuning._simplex(np.array([0.5, 0.95, 0.9]))
    assert np.allclose(simplex, [[0.5, 0.95, 0.9], [0.6, 0.95, 0.9], [0.5, 0.85, 0.9], [0.5, 0.95, 1.0]])


@pytest.mark.parametrize('name', ['elo', 'glicko2', 'bradley_terry'])
def test_search_spaces_are_consistent(name):
    """Test that each model's grid lies within its bounds, and log-scale bounds are positive."""
    space = MODELS[name]
    assert set(space.grid) == set(space.bounds)
    for parameter, (low, high) in space.bounds.items():
        assert low < high
        assert all(low <= value <= high for value in space.grid[parameter])
        assert parameter not in LOG_SCALE or low > 0


# === All models ===


def test_evaluate_all_order(monkeypatch):
    """Test that every model is tuned for every identity, models first."""
    monkeypatch.setattr(tuning, 'tune', lambda _matches, _lineups, model, identity: (model, identity))
    matches = _history([0, 200])  # one played match in each season
    assert evaluate_all(matches, identify_lineups(matches), ['uniform', 'elo'], ['name', 'players'], workers=1) == [
        ('uniform', 'name'),
        ('uniform', 'players'),
        ('elo', 'name'),
        ('elo', 'players'),
    ]


class _FakePool:
    """Stands in for ProcessPoolExecutor (once given where to record its size): maps in this process."""

    def __init__(self, sizes, max_workers):
        sizes.append(max_workers)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    @staticmethod
    def map(function, *iterables):
        """Map in this process."""
        return map(function, *iterables)


@pytest.mark.parametrize(('cpus', 'sizes'), [(3, [2]), (1, [])])
def test_default_workers_one_per_cpu(monkeypatch, cpus, sizes):
    """Test that by default there is a worker per CPU up to one per tuning, and none on a single CPU."""
    pools = []
    monkeypatch.setattr(tuning, 'ProcessPoolExecutor', partial(_FakePool, pools))
    monkeypatch.setattr(tuning, 'cpu_count', lambda: cpus)
    monkeypatch.setattr(tuning, 'tune', lambda _matches, _lineups, model, identity: (model, identity))
    matches = _history([0, 200])
    tuned = evaluate_all(matches, identify_lineups(matches), ['uniform'], ['name', 'players'])
    assert tuned == [('uniform', 'name'), ('uniform', 'players')]
    assert pools == sizes


def test_one_worker_starts_no_pool(monkeypatch):
    """Test that a single worker tunes in this process."""
    pools = []
    monkeypatch.setattr(tuning, 'ProcessPoolExecutor', partial(_FakePool, pools))
    monkeypatch.setattr(tuning, 'tune', lambda _matches, _lineups, model, identity: (model, identity))
    matches = _history([0, 200])
    assert evaluate_all(matches, identify_lineups(matches), ['uniform'], ['name', 'players'], workers=1) == [
        ('uniform', 'name'),
        ('uniform', 'players'),
    ]
    assert pools == []


def test_evaluate_all_without_models():
    """Test that tuning no model gives no result, without starting workers."""
    matches = _history([0, 200])
    assert evaluate_all(matches, identify_lineups(matches), [], ['name']) == []


def test_worker_processes_give_the_same_results():
    """Test that tuning in worker processes gives exactly the results of tuning in this process."""
    matches = _history(range(0, 200, 10), first_loses=range(0, 200, 30))
    lineups = identify_lineups(matches)
    serial = evaluate_all(matches, lineups, ['uniform', 'elo'], ['name', 'players'], workers=1)
    assert evaluate_all(matches, lineups, ['uniform', 'elo'], ['name', 'players'], workers=2) == serial
    assert len(serial[2].trials) > len(list(MODELS['elo'].grid['k'])) * 9  # the grid, then the search


@pytest.mark.parametrize(
    ('days', 'missing'), [((0,), 'RLCS 2026'), ((200,), 'RLCS 2025'), ((), 'RLCS 2025, RLCS 2026')]
)
def test_evaluate_all_needs_both_seasons(days, missing):
    """Test that a history without a played match in the tuning or evaluation season is a data error."""
    matches = _history(days)
    matches.append(replace(_history([201])[0], walkover=True))  # walkovers don't count
    with pytest.raises(DataSourceError, match=f'No played match of {missing}'):
        evaluate_all(matches, identify_lineups(matches))
