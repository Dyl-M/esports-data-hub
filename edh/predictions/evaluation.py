"""Walk-forward evaluation of rating models on the match history.

Matches are taken in date order. Each played match is predicted with the ratings built from the matches before it,
then used to update them, so every prediction is one that could have been made on that day. Walkovers are neither
predicted nor learned from. Hyperparameters are picked on the tuning season, and the evaluation season is scored once
with them.
"""

# Standard library
import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import product

# Local
from ..exceptions import DataSourceError
from .constants import (
    BT_GRID,
    CALIBRATION_BINS,
    ELO_GRID,
    EVALUATION_SEASON,
    EXPERIENCED_MATCHES,
    GLICKO2_GRID,
    GROUP_ALL,
    GROUP_EXPERIENCED,
    GROUP_INEXPERIENCED,
    GROUP_TIER,
    IDENTITIES,
    IDENTITY_LINEUP,
    IDENTITY_NAME,
    MODEL_BRADLEY_TERRY,
    MODEL_ELO,
    MODEL_GLICKO2,
    MODEL_UNIFORM,
    PROBABILITY_FLOOR,
    TUNING_SEASON,
)
from .lineups import season_of
from .models import Lineups, PlayedMatch, start_order
from .ratings import BradleyTerry, Elo, Glicko2, Members, RatingModel, Uniform
from .series import series_probability

# Model factories and their hyperparameter grids
MODELS: dict[str, tuple[Callable[..., RatingModel], Mapping[str, tuple[float, ...]]]] = {
    MODEL_UNIFORM: (Uniform, {}),
    MODEL_ELO: (Elo, ELO_GRID),
    MODEL_GLICKO2: (Glicko2, GLICKO2_GRID),
    MODEL_BRADLEY_TERRY: (BradleyTerry, BT_GRID),
}

# === Predictions ===


@dataclass(frozen=True)
class Prediction:
    """A match predicted before it was played.

    Attributes:
        match_id: LPDB match id.
        season: RLCS season of the match.
        tier: Liquipedia tier.
        experienced: Whether both lineups had played at least `EXPERIENCED_MATCHES` matches before.
        game_probability: Predicted probability that the first team wins a game.
        series_probability: Predicted probability that the first team wins the series.
        games: Games won by the first and second teams.
    """

    match_id: str
    season: int
    tier: str
    experienced: bool
    game_probability: float
    series_probability: float
    games: tuple[int, int]

    @property
    def first_won(self) -> bool:
        """Whether the first team won the series (series have no draw)."""
        return self.games[0] == max(self.games)


def walk_forward(
    matches: Iterable[PlayedMatch], lineups: Lineups, identity: str, model: RatingModel
) -> list[Prediction]:
    """Predict every played match with the ratings of the matches before it, then learn from it.

    Args:
        matches: The history.
        lineups: Lineup identity over the same history.
        identity: Who the ratings follow: `name`, `lineup` or `players`.
        model: A fresh rating model.

    Returns:
        One prediction per played match (walkovers excluded), in date order.
    """
    predictions = []
    seasons: dict[str, int] = {}
    played: Counter[int] = Counter()
    predecessor = _predecessors(identity, lineups)
    for match in sorted(matches, key=start_order):
        season = season_of(match.played_at.date())
        first, second = (_members(identity, match, index, lineups) for index in (0, 1))
        for members in (first, second):
            _register(model, members, season, seasons, predecessor)
        lineup_ids = lineups.sides[match.match_id]
        if not match.walkover:
            model.prepare(match.played_at)
            p = model.game_probability(first, second)
            games = (match.opponents[0].score, match.opponents[1].score)
            experienced = min(played[lineup_id] for lineup_id in lineup_ids) >= EXPERIENCED_MATCHES
            predictions.append(
                Prediction(match.match_id, season, match.tier, experienced, p, series_probability(p, max(games)), games)
            )
            model.update(first, second, games, match.played_at)
        played.update(lineup_ids)
    return predictions


def _members(identity: str, match: PlayedMatch, index: int, lineups: Lineups) -> Members:
    """The rated members of one side: its name, its lineup, or its roster's players (its name without any roster)."""
    opponent = match.opponents[index]
    if identity == IDENTITY_NAME:
        return (opponent.name,)
    if identity == IDENTITY_LINEUP:
        return (str(lineups.sides[match.match_id][index]),)
    roster = lineups.rosters[match.match_id][index]
    if not roster:
        return (opponent.name,)
    players: list[str] = sorted(roster)
    return tuple(players)


def _predecessors(identity: str, lineups: Lineups) -> Callable[[str], str | None]:
    """How to find the member a new member inherits from: only lineups have predecessors."""
    if identity != IDENTITY_LINEUP:
        return lambda _: None

    def predecessor(member: str) -> str | None:
        inherited = lineups.by_id[int(member)].predecessor
        return None if inherited is None else str(inherited)

    return predecessor


def _register(
    model: RatingModel,
    members: Members,
    season: int,
    seasons: dict[str, int],
    predecessor: Callable[[str], str | None],
) -> None:
    """Introduce new members, and start a new season for members last seen in an earlier one.

    A new member inheriting from a predecessor last seen in an earlier season starts the new season too, like the
    predecessor would have: a lineup renamed over the off-season is pulled toward the mean as if it had kept its name.
    """
    for member in members:
        if member not in seasons:
            inherited = predecessor(member)
            model.introduce(member, inherited)
            if inherited is not None and seasons.get(inherited, season) != season:
                model.new_season(member)
        elif seasons[member] != season:
            model.new_season(member)
        seasons[member] = season


# === Scores ===


@dataclass(frozen=True)
class Scores:
    """How good a set of predictions is (lower log losses and Brier score are better).

    Attributes:
        matches: Matches scored.
        log_loss: Mean log loss of the series winner.
        brier: Mean squared error of the series winner probability.
        accuracy: Share of series whose winner was given more than 1/2 (a prediction of exactly 1/2 counts half).
        game_log_loss: Mean log loss per game.
    """

    matches: int
    log_loss: float
    brier: float
    accuracy: float
    game_log_loss: float


@dataclass(frozen=True)
class CalibrationBin:
    """Series predictions grouped by predicted probability that the first team wins.

    Attributes:
        low: Lower bound of the bin (included).
        high: Upper bound of the bin (excluded, except 1).
        matches: Predictions in the bin.
        predicted: Mean predicted probability.
        observed: Share of these series the first team won.
    """

    low: float
    high: float
    matches: int
    predicted: float
    observed: float


def score(predictions: Sequence[Prediction]) -> Scores:
    """Score predictions against the actual results.

    Args:
        predictions: Predictions to score (at least one).

    Returns:
        Their scores.

    Raises:
        ValueError: If there is no prediction to score.
    """
    if not predictions:
        raise ValueError('no prediction to score')
    log_loss = brier = correct = game_loss = 0.0
    games = 0
    for prediction in predictions:
        p, outcome = _clip(prediction.series_probability), float(prediction.first_won)
        log_loss -= outcome * math.log(p) + (1 - outcome) * math.log(1 - p)
        brier += (prediction.series_probability - outcome) ** 2
        favorite = ((prediction.series_probability > 0.5) + (prediction.series_probability >= 0.5)) / 2  # 1, 1/2 or 0
        correct += 1 - abs(outcome - favorite)
        q = _clip(prediction.game_probability)
        won, lost = prediction.games
        game_loss -= won * math.log(q) + lost * math.log(1 - q)
        games += won + lost
    count = len(predictions)
    return Scores(count, log_loss / count, brier / count, correct / count, game_loss / games)


def calibration(predictions: Sequence[Prediction], bins: int = CALIBRATION_BINS) -> list[CalibrationBin]:
    """Group series predictions into equal-width bins of predicted probability.

    Args:
        predictions: Predictions to group.
        bins: Number of bins over [0, 1].

    Returns:
        The non-empty bins, lowest first.
    """
    grouped: dict[int, list[Prediction]] = {}
    for prediction in predictions:
        grouped.setdefault(min(int(prediction.series_probability * bins), bins - 1), []).append(prediction)
    return [
        CalibrationBin(
            index / bins,
            (index + 1) / bins,
            len(group),
            sum(prediction.series_probability for prediction in group) / len(group),
            sum(prediction.first_won for prediction in group) / len(group),
        )
        for index, group in sorted(grouped.items())
    ]


def breakdown(predictions: Sequence[Prediction]) -> dict[str, Scores]:
    """Score predictions overall, per tier, and for experienced / new lineups.

    Args:
        predictions: Predictions to score.

    Returns:
        Scores per group (`all`, `tier <n>`, `experienced`, `inexperienced`), empty groups left out.
    """
    groups: dict[str, list[Prediction]] = {GROUP_ALL: list(predictions)}
    for prediction in predictions:
        groups.setdefault(GROUP_TIER.format(prediction.tier), []).append(prediction)
        groups.setdefault(GROUP_EXPERIENCED if prediction.experienced else GROUP_INEXPERIENCED, []).append(prediction)
    return {name: score(group) for name, group in groups.items() if group}


def first_team_share(predictions: Sequence[Prediction]) -> float:
    """Return the share of series won by the team Liquipedia lists first (a bias of the data, not of a model).

    Args:
        predictions: Predictions of played series (at least one).

    Returns:
        The share of them won by the first team.
    """
    return sum(prediction.first_won for prediction in predictions) / len(predictions)


def _clip(p: float) -> float:
    """Keep a probability away from 0 and 1 so its log stays finite."""
    return min(max(p, PROBABILITY_FLOOR), 1 - PROBABILITY_FLOOR)


# === Tuning ===


@dataclass(frozen=True)
class Result:
    """A model and identity, tuned on the tuning season, with its predictions of the evaluation season.

    Attributes:
        model: Model name.
        identity: Who the ratings follow.
        params: Hyperparameters picked on the tuning season.
        tuning: Scores on the tuning season with those hyperparameters.
        predictions: Predictions of the evaluation season.
    """

    model: str
    identity: str
    params: Mapping[str, float]
    tuning: Scores
    predictions: tuple[Prediction, ...]


def tune(
    matches: Sequence[PlayedMatch],
    lineups: Lineups,
    model: str,
    identity: str,
    seasons: tuple[int, int] = (TUNING_SEASON, EVALUATION_SEASON),
) -> Result:
    """Run a model over its grid, keep the hyperparameters with the lowest series log loss on the tuning season.

    Args:
        matches: The history.
        lineups: Lineup identity over the same history.
        model: Model name (a key of `MODELS`).
        identity: Who the ratings follow.
        seasons: Tuning season, then evaluation season.

    Returns:
        The tuned model and its predictions of the evaluation season.
    """
    factory, grid = MODELS[model]
    tuning_season, evaluation_season = seasons
    runs = []
    for params in _combinations(grid):
        predictions = walk_forward(matches, lineups, identity, factory(**params))
        tuning = score([prediction for prediction in predictions if prediction.season == tuning_season])
        runs.append((tuning, params, predictions))
    tuning, best, predictions = min(runs, key=lambda run: run[0].log_loss)
    evaluated = tuple(prediction for prediction in predictions if prediction.season == evaluation_season)
    return Result(model, identity, best, tuning, evaluated)


def evaluate_all(
    matches: Sequence[PlayedMatch],
    lineups: Lineups,
    models: Sequence[str] = tuple(MODELS),
    identities: Sequence[str] = IDENTITIES,
) -> list[Result]:
    """Tune every model for every identity.

    Args:
        matches: The history.
        lineups: Lineup identity over the same history.
        models: Model names.
        identities: Identities compared.

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
    return [tune(matches, lineups, model, identity) for model, identity in product(models, identities)]


def _combinations(grid: Mapping[str, tuple[float, ...]]) -> list[dict[str, float]]:
    """Every combination of the grid's values (a single empty one for a model without hyperparameters)."""
    return [{name: values[index] for index, name in enumerate(grid)} for values in product(*grid.values())]
