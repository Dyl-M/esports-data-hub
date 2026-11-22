"""Forecasts: per-game win probabilities of matches not played yet.

The history before the forecast's time is replayed exactly as the walk-forward evaluation replays it
(`evaluation.Replay`), then each team is rated as it stands: its latest lineup and roster in the history, or a roster
given explicitly. A forecast is therefore the prediction the evaluation would have made for that match. How sides are
resolved and why: `_docs/predictions.md`, *Forecasts*.
"""

# Standard library
import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# Local
from .evaluation import Replay, side_members
from .lineups import season_of
from .models import Lineups, PlayedMatch, start_order
from .ratings import Members, RatingModel
from .series import series_probability

# === Models ===


@dataclass(frozen=True)
class Side:
    """A team as a forecast rates it.

    Attributes:
        team: Team name, as Liquipedia gives it.
        lineup_id: Its latest lineup in the history; None for a team the history has never seen.
        roster: Player pages rated by the `players` identity: the roster given, else the team's latest in the history;
            empty when unknown (the side is then rated by its name).
    """

    team: str
    lineup_id: int | None
    roster: frozenset[str]


@dataclass(frozen=True)
class Forecast:
    """A match not played yet, predicted.

    Attributes:
        first: The first team.
        second: The second team.
        game_probability: Probability that the first team wins a game.
        unseen: Members of each side the history never rated (cold starts), sorted.
    """

    first: Side
    second: Side
    game_probability: float
    unseen: tuple[Members, Members]

    def series_probability(self, wins_needed: int) -> float:
        """Return the probability that the first team wins the series.

        Args:
            wins_needed: Games needed to win the series (3 in a best of 5).

        Returns:
            Probability that the first team reaches `wins_needed` first.
        """
        return series_probability(self.game_probability, wins_needed)


# === Forecasts ===


class Forecaster:
    """A rating model trained on the history before a time, predicting any pair of teams at that time."""

    def __init__(
        self, matches: Iterable[PlayedMatch], lineups: Lineups, identity: str, model: RatingModel, when: dt.datetime
    ) -> None:
        """Replay the matches starting before `when`.

        Args:
            matches: The history (later matches are left out).
            lineups: Lineup identity over the same history.
            identity: Who the ratings follow: `name`, `lineup` or `players`.
            model: A fresh rating model.
            when: Time of the forecasts, timezone-aware: every member is rated in its season.
        """
        past = sorted((match for match in matches if match.played_at < when), key=start_order)
        self._replay = Replay(lineups, identity, model)
        for match in past:
            self._replay.play(match)
        self._rated = self._replay.members
        self._latest = _latest_sides(past, lineups)
        self._identity, self._when = identity, when

    def side(self, team: str, roster: Iterable[str] | None = None) -> Side:
        """Return how a team is rated: its latest lineup and roster in the history, the roster replaced when given.

        A given roster changes the `players` identity only: `name` and `lineup` keep following the team's name.

        Args:
            team: Team name.
            roster: Player pages of its roster; None to take the latest one in the history.

        Returns:
            The side.
        """
        lineup_id, latest = self._latest.get(team, (None, frozenset()))
        return Side(team, lineup_id, latest if roster is None else frozenset(roster))

    def forecast(self, first: Side, second: Side) -> Forecast:
        """Predict a match between two sides.

        Members are registered as at a match of the forecast's season: new ones introduced, those last seen in an
        earlier season pulled toward the mean, as the walk-forward evaluation would at their next match.

        Args:
            first: The first team.
            second: The second team.

        Returns:
            The forecast.
        """
        sides = [side_members(self._identity, side.team, side.lineup_id, side.roster) for side in (first, second)]
        first_unseen, second_unseen = (self._unseen(members) for members in sides)
        season = season_of(self._when.date())
        for members in sides:
            self._replay.register(members, season)
        model = self._replay.model
        model.prepare(self._when)
        return Forecast(first, second, model.game_probability(*sides), (first_unseen, second_unseen))

    def _unseen(self, members: Members) -> Members:
        """The members the history never rated, sorted."""
        return tuple(sorted(set(members) - self._rated))


def _latest_sides(matches: Sequence[PlayedMatch], lineups: Lineups) -> dict[str, tuple[int, frozenset[str]]]:
    """The lineup and roster of each team name at its latest match, from matches in date order."""
    latest = {}
    for match in matches:
        for index, opponent in enumerate(match.opponents):
            latest[opponent.name] = (lineups.sides[match.match_id][index], lineups.rosters[match.match_id][index])
    return latest
