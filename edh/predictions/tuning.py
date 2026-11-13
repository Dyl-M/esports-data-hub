"""Hyperparameter tuning of the rating models: a small grid, refined by a bounded search.

Each model is tuned for each identity on the tuning season. Every combination of its grid is run, then a bounded
Nelder-Mead search starts from the best of them. Each run is a full walk-forward pass scored by its series log loss on
the tuning season, and the lowest wins, so the pick is never worse than the grid's. The evaluation season is then
scored once with it. Tunings of different models and identities run in worker processes.
"""

# Standard library
import math
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from itertools import product
from multiprocessing import cpu_count

# Third-party
import numpy as np
from scipy.optimize import minimize

# Local
from ..exceptions import DataSourceError
from .constants import (
    BT_BOUNDS,
    BT_GRID,
    ELO_BOUNDS,
    ELO_GRID,
    EVALUATION_SEASON,
    GLICKO2_BOUNDS,
    GLICKO2_GRID,
    IDENTITIES,
    LOG_SCALE,
    MODEL_BRADLEY_TERRY,
    MODEL_ELO,
    MODEL_GLICKO2,
    MODEL_UNIFORM,
    TUNING_DIGITS,
    TUNING_LOSS_TOLERANCE,
    TUNING_MAX_EVALUATIONS,
    TUNING_SEASON,
    TUNING_STEP,
    TUNING_TOLERANCE,
)
from .evaluation import Prediction, Scores, score, walk_forward
from .lineups import season_of
from .models import Lineups, PlayedMatch
from .ratings import BradleyTerry, Elo, Glicko2, RatingModel, Uniform

# === Search spaces ===

Params = dict[str, float]  # hyperparameters by name


@dataclass(frozen=True)
class SearchSpace:
    """A model and where its hyperparameters are searched.

    Attributes:
        factory: Builds a fresh model from hyperparameters.
        grid: Values tried first per hyperparameter, every combination run.
        bounds: Lowest and highest value of each hyperparameter in the search that follows.
    """

    factory: Callable[..., RatingModel]
    grid: Mapping[str, tuple[float, ...]] = field(default_factory=dict)
    bounds: Mapping[str, tuple[float, float]] = field(default_factory=dict)


MODELS: dict[str, SearchSpace] = {
    MODEL_UNIFORM: SearchSpace(Uniform),
    MODEL_ELO: SearchSpace(Elo, ELO_GRID, ELO_BOUNDS),
    MODEL_GLICKO2: SearchSpace(Glicko2, GLICKO2_GRID, GLICKO2_BOUNDS),
    MODEL_BRADLEY_TERRY: SearchSpace(BradleyTerry, BT_GRID, BT_BOUNDS),
}

# === Results ===


@dataclass(frozen=True)
class Trial:
    """A run of the tuning.

    Attributes:
        params: Hyperparameters run.
        log_loss: Their series log loss on the tuning season.
    """

    params: Params
    log_loss: float


@dataclass(frozen=True)
class Result:
    """A model and identity, tuned on the tuning season, with its predictions of the evaluation season.

    Attributes:
        model: Model name.
        identity: Who the ratings follow.
        params: Hyperparameters picked on the tuning season.
        tuning: Scores on the tuning season with those hyperparameters.
        predictions: Predictions of the evaluation season.
        trials: Every run of the tuning, in the order run.
        at_bound: Hyperparameters picked on a bound of their search.
    """

    model: str
    identity: str
    params: Params
    tuning: Scores
    predictions: tuple[Prediction, ...]
    trials: tuple[Trial, ...]
    at_bound: tuple[str, ...]


# === Tuning ===


@dataclass(frozen=True)
class _Run:
    """A walk-forward pass: its hyperparameters, its scores on the tuning season and all its predictions."""

    params: Params
    tuning: Scores
    predictions: list[Prediction]


@dataclass
class _Search:
    """The runs of one tuning: each set of hyperparameters run once, every new best kept with its predictions."""

    matches: Sequence[PlayedMatch]
    lineups: Lineups
    identity: str
    space: SearchSpace
    trials: list[Trial] = field(default_factory=list)
    improvements: list[_Run] = field(default_factory=list)
    _seen: dict[tuple[tuple[str, float], ...], float] = field(default_factory=dict, init=False, repr=False)

    def run(self, params: Params) -> float:
        """Run a walk-forward pass with these hyperparameters (once), return its tuning log loss."""
        key = tuple(sorted(params.items()))
        if key not in self._seen:
            predictions = walk_forward(self.matches, self.lineups, self.identity, self.space.factory(**params))
            tuning = score([prediction for prediction in predictions if prediction.season == TUNING_SEASON])
            self._seen[key] = tuning.log_loss
            self.trials.append(Trial(params, tuning.log_loss))
            if not self.improvements or tuning.log_loss < self.best.tuning.log_loss:
                self.improvements.append(_Run(params, tuning, predictions))
        return self._seen[key]

    @property
    def best(self) -> _Run:
        """The run with the lowest tuning log loss (the first of them on a tie)."""
        return self.improvements[-1]


def tune(matches: Sequence[PlayedMatch], lineups: Lineups, model: str, identity: str) -> Result:
    """Run a model over its grid, then search from the best point; keep the lowest series log loss of the tuning season.

    Args:
        matches: The history.
        lineups: Lineup identity over the same history.
        model: Model name (a key of `MODELS`).
        identity: Who the ratings follow.

    Returns:
        The tuned model and its predictions of the evaluation season.
    """
    space = MODELS[model]
    search = _Search(matches, lineups, identity, space)
    for params in _combinations(space.grid):
        search.run(params)
    if space.bounds:
        _refine(search)
    best = search.best
    evaluated = tuple(prediction for prediction in best.predictions if prediction.season == EVALUATION_SEASON)
    at_bound = _at_bound(best.params, space.bounds)
    return Result(model, identity, best.params, best.tuning, evaluated, tuple(search.trials), at_bound)


def _simplex(start: np.ndarray) -> np.ndarray:
    """The search's first simplex: the start, then a step of `TUNING_STEP` along each axis (back when it would leave 1).

    Args:
        start: A point of the unit cube.

    Returns:
        One row per vertex, the start first.
    """
    vertices = [start]
    for index, value in enumerate(start):
        step = np.zeros(len(start))
        step[index] = -TUNING_STEP if value + TUNING_STEP > 1 else TUNING_STEP
        vertices.append(start + step)
    return np.array(vertices)


def _refine(search: _Search) -> None:
    """Bounded Nelder-Mead search from the best run, each hyperparameter scaled to [0, 1] over its bounds."""
    bounds = search.space.bounds
    best = search.best.params
    start = np.array([_to_unit(name, best[name], bounds[name]) for name in bounds])

    def loss(point: np.ndarray) -> float:
        """Tuning log loss of a point of the unit cube."""
        return search.run(
            {name: _from_unit(name, float(point[index]), bounds[name]) for index, name in enumerate(bounds)}
        )

    minimize(
        loss,
        start,
        method='Nelder-Mead',
        bounds=[(0.0, 1.0)] * len(start),
        options={
            'initial_simplex': _simplex(start),
            'maxfev': TUNING_MAX_EVALUATIONS,
            'xatol': TUNING_TOLERANCE,
            'fatol': TUNING_LOSS_TOLERANCE,
        },
    )


def _to_unit(name: str, value: float, bounds: tuple[float, float]) -> float:
    """Position of a value between its bounds, from 0 to 1 (on a log scale for `LOG_SCALE` hyperparameters)."""
    low, high = bounds
    if name in LOG_SCALE:
        return math.log(value / low) / math.log(high / low)
    return (value - low) / (high - low)


def _from_unit(name: str, x: float, bounds: tuple[float, float]) -> float:
    """The value at position x between its bounds, rounded to `TUNING_DIGITS` significant digits, kept in bounds."""
    low, high = bounds
    value = low * math.pow(high / low, x) if name in LOG_SCALE else low + x * (high - low)
    return min(max(float(f'{value:.{TUNING_DIGITS}g}'), low), high)


def _at_bound(params: Params, bounds: Mapping[str, tuple[float, float]]) -> tuple[str, ...]:
    """The hyperparameters within `TUNING_TOLERANCE` (a share of their scaled range) of one of their bounds."""
    return tuple(
        name
        for name, limits in bounds.items()
        if not TUNING_TOLERANCE < _to_unit(name, params[name], limits) < 1 - TUNING_TOLERANCE
    )


def _combinations(grid: Mapping[str, tuple[float, ...]]) -> list[Params]:
    """Every combination of the grid's values (a single empty one for a model without hyperparameters)."""
    return [{name: values[index] for index, name in enumerate(grid)} for values in product(*grid.values())]


# === All models ===


def evaluate_all(
    matches: Sequence[PlayedMatch],
    lineups: Lineups,
    models: Sequence[str] = tuple(MODELS),
    identities: Sequence[str] = IDENTITIES,
    workers: int | None = None,
) -> list[Result]:
    """Tune every model for every identity, in worker processes.

    Args:
        matches: The history.
        lineups: Lineup identity over the same history.
        models: Model names.
        identities: Identities compared.
        workers: Worker processes (None: one per CPU; 1: tune in the calling process). Results don't depend on it.

    Returns:
        One result per model and identity, in that order.

    Raises:
        DataSourceError: If the tuning or the evaluation season has no played match in the history.
    """
    played = {season_of(match.played_at.date()) for match in matches if not match.walkover}
    missing = [season for season in (TUNING_SEASON, EVALUATION_SEASON) if season not in played]
    if missing:
        seasons = ', '.join(f'RLCS {season}' for season in missing)
        raise DataSourceError(f'No played match of {seasons} in the history: run `python -m edh.predictions.run`')
    tasks = list(product(models, identities))
    task_models, task_identities = [model for model, _ in tasks], [identity for _, identity in tasks]
    run = partial(tune, matches, lineups)
    workers = min(workers or cpu_count(), len(tasks))
    if workers <= 1:
        return list(map(run, task_models, task_identities))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, task_models, task_identities))
