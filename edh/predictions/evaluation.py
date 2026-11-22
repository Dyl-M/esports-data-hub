"""Walk-forward evaluation of rating models on the match history.

Matches are taken in date order. Each played match is predicted with the ratings built from the matches before it,
then used to update them, so every prediction is one that could have been made on that day. Walkovers are neither
predicted nor learned from. How the hyperparameters are picked: `tuning.py`.
"""

# Standard library
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# Local
from .constants import (
    CALIBRATION_BINS,
    EXPERIENCED_MATCHES,
    GROUP_ALL,
    GROUP_EXPERIENCED,
    GROUP_INEXPERIENCED,
    GROUP_TIER,
    IDENTITY_LINEUP,
    IDENTITY_PLAYERS,
    PROBABILITY_FLOOR,
)
from .lineups import season_of
from .models import Lineups, PlayedMatch, start_order
from .ratings import Members, RatingModel
from .series import series_probability

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
    replay = Replay(lineups, identity, model)
    return [prediction for match in sorted(matches, key=start_order) if (prediction := replay.play(match))]


class Replay:
    """A rating model taken through the history one match at a time, as the walk-forward evaluation does.

    The forecasts (`forecast.py`) replay the history the same way, then rate sides that have not played yet, so a
    forecast is the prediction the evaluation would have made.
    """

    def __init__(self, lineups: Lineups, identity: str, model: RatingModel) -> None:
        """Start with a fresh model and no match played.

        Args:
            lineups: Lineup identity over the history to replay.
            identity: Who the ratings follow: `name`, `lineup` or `players`.
            model: A fresh rating model.
        """
        self.model = model
        self._lineups, self._identity = lineups, identity
        self._seasons: dict[str, int] = {}  # season of each member's latest match
        self._played: Counter[int] = Counter()  # matches played by each lineup
        self._predecessors = _predecessors(identity, lineups)

    @property
    def members(self) -> frozenset[str]:
        """Members rated so far."""
        return frozenset(self._seasons)

    def play(self, match: PlayedMatch) -> Prediction | None:
        """Predict a match with the ratings so far, then learn from it.

        Args:
            match: The next match of the history, in date order.

        Returns:
            Its prediction; None for a walkover (neither predicted nor learned from, its sides still registered).
        """
        season = season_of(match.played_at.date())
        first, second = (_members(self._identity, match, index, self._lineups) for index in (0, 1))
        for members in (first, second):
            self.register(members, season)
        lineup_ids = self._lineups.sides[match.match_id]
        prediction = None
        if not match.walkover:
            self.model.prepare(match.played_at)
            p = self.model.game_probability(first, second)
            games = (match.opponents[0].score, match.opponents[1].score)
            experienced = min(self._played[lineup_id] for lineup_id in lineup_ids) >= EXPERIENCED_MATCHES
            prediction = Prediction(
                match.match_id, season, match.tier, experienced, p, series_probability(p, max(games)), games
            )
            self.model.update(first, second, games, match.played_at)
        self._played.update(lineup_ids)
        return prediction

    def register(self, members: Members, season: int) -> None:
        """Introduce new members, and start a new season for members last seen in an earlier one.

        A new member inheriting from a predecessor last seen in an earlier season starts the new season too, like the
        predecessor would have: a lineup renamed over the off-season is pulled toward the mean as if it had kept its
        name.

        Args:
            members: The members of a side about to play.
            season: RLCS season of the match.
        """
        for member in members:
            if member not in self._seasons:
                inherited = self._predecessors.get(member)
                self.model.introduce(member, inherited)
                if inherited is not None and self._seasons.get(inherited, season) != season:
                    self.model.new_season(member)
            elif self._seasons[member] != season:
                self.model.new_season(member)
            self._seasons[member] = season


def _members(identity: str, match: PlayedMatch, index: int, lineups: Lineups) -> Members:
    """The rated members of one side: its name, its lineup, or its roster's players (its name without any roster)."""
    opponent = match.opponents[index]
    lineup_id = lineups.sides[match.match_id][index]
    return side_members(identity, opponent.name, lineup_id, lineups.rosters[match.match_id][index])


def side_members(identity: str, name: str, lineup_id: int | None, roster: frozenset[str]) -> Members:
    """Return the rated members of a side: its name, its lineup, or its roster's players.

    Args:
        identity: Who the ratings follow: `name`, `lineup` or `players`.
        name: Team name.
        lineup_id: Its lineup; None for a team the history has never seen (rated by its name).
        roster: Player pages of its roster; empty when unknown (rated by its name).

    Returns:
        The members whose ratings make the side's.
    """
    if identity == IDENTITY_LINEUP and lineup_id is not None:
        return (str(lineup_id),)
    if identity == IDENTITY_PLAYERS and roster:
        players: list[str] = sorted(roster)
        return tuple(players)
    return (name,)


def _predecessors(identity: str, lineups: Lineups) -> Mapping[str, str]:
    """The member each new member inherits from, when it has one: only lineups have predecessors."""
    if identity != IDENTITY_LINEUP:
        return {}
    return {
        str(lineup.lineup_id): str(lineup.predecessor) for lineup in lineups.by_id if lineup.predecessor is not None
    }


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
