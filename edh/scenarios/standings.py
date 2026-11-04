"""League standings and Liquipedia-style tiebreakers.

The ranking follows Liquipedia's `Module:Standings` resolution: every team starts in one tied group, the first
tiebreaker splits it into groups of equal value (best first), and each group still tied moves on to the **next**
tiebreaker (earlier ones are not applied again). Mini-league (`ml.`) tiebreakers are computed on the series played
between the teams of the current group only. A group still tied after the last tiebreaker shares its ranks.

Goal-based (`round*`) tiebreakers need the goals of every game in their scope. When some are unknown (a simulated
series), the group cannot be split any further and stays tied: the tie is reported as unresolved instead of guessed.
"""

# Standard library
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

# Local
from .constants import CONTEXT_FULL, ROUND_TIEBREAKERS, TIEBREAKER_MANUAL
from .models import RankGroup, Series, Tiebreaker

TiebreakValue = int | tuple[int, int]


@dataclass
class TeamRecord:
    """Series, game and goal record of a team over a set of finished series.

    Attributes:
        match_wins: Series won.
        match_losses: Series lost.
        game_wins: Games won.
        game_losses: Games lost.
        round_wins: Goals scored.
        round_losses: Goals conceded.
        rounds_known: Whether the goals of every counted game are known.
    """

    match_wins: int = 0
    match_losses: int = 0
    game_wins: int = 0
    game_losses: int = 0
    round_wins: int = 0
    round_losses: int = 0
    rounds_known: bool = True


def tally(series: Iterable[Series], teams: Collection[str]) -> dict[str, TeamRecord]:
    """Compute the record of each team over the finished series among `teams`.

    Args:
        series: Series to count; unfinished ones and those involving a team outside `teams` are ignored.
        teams: Teams to compute a record for.

    Returns:
        The record of each team in `teams`.
    """
    records = {team: TeamRecord() for team in teams}
    for item in series:
        if not item.finished or item.team_1 not in records or item.team_2 not in records:
            continue
        for team, won, lost, side in (
            (item.team_1, item.score_1, item.score_2, 0),
            (item.team_2, item.score_2, item.score_1, 1),
        ):
            record = records[team]
            record.match_wins += won > lost
            record.match_losses += won < lost
            record.game_wins += won
            record.game_losses += lost
            if item.goals is None:
                record.rounds_known = False
            else:
                record.round_wins += sum(game[side] for game in item.goals)
                record.round_losses += sum(game[1 - side] for game in item.goals)
    return records


_RECORD_VALUES: dict[str, Callable[[TeamRecord], TiebreakValue]] = {
    'matchscore': lambda record: (record.match_wins, -record.match_losses),  # wins first, then fewer losses
    'matchdiff': lambda record: record.match_wins - record.match_losses,
    'matchwins': lambda record: record.match_wins,
    'gamescore': lambda record: (record.game_wins, -record.game_losses),
    'gamediff': lambda record: record.game_wins - record.game_losses,
    'gamewins': lambda record: record.game_wins,
    'rounddiff': lambda record: record.round_wins - record.round_losses,
    'roundwins': lambda record: record.round_wins,
}


def _value(tiebreaker: Tiebreaker, record: TeamRecord, manual_points: int) -> TiebreakValue | None:
    """Return the value of a tiebreaker for one team (higher is better), or None when the goals are unknown."""
    if tiebreaker.name == TIEBREAKER_MANUAL:
        return manual_points
    if tiebreaker.name in ROUND_TIEBREAKERS and not record.rounds_known:
        return None
    return _RECORD_VALUES[tiebreaker.name](record)


def rank(
    series: Sequence[Series],
    teams: Collection[str],
    tiebreakers: Sequence[Tiebreaker],
    manual_tiebreakers: Mapping[str, int] | None = None,
) -> list[RankGroup]:
    """Rank teams from their results.

    Args:
        series: League series; only finished ones count.
        teams: Teams to rank.
        tiebreakers: Ordered tiebreakers.
        manual_tiebreakers: Manual tiebreak points per team (0 when absent).

    Returns:
        Groups of teams in ranking order; a group of several teams is a tie the tiebreakers cannot settle.
    """
    resolver = _TieResolver(series, teams, tiebreakers, manual_tiebreakers or {})
    groups: list[RankGroup] = []
    position = 1
    for group in resolver.resolve(tuple(sorted(teams)), 0):
        groups.append(RankGroup(rank=position, teams=group))
        position += len(group)
    return groups


class _TieResolver:
    """Split tied groups with the tiebreakers, following Liquipedia's resolution order."""

    def __init__(
        self,
        series: Sequence[Series],
        teams: Collection[str],
        tiebreakers: Sequence[Tiebreaker],
        manual_tiebreakers: Mapping[str, int],
    ) -> None:
        """Prepare the full-league records once; mini-league records are computed per group."""
        self.series = series
        self.tiebreakers = tiebreakers
        self.manual = manual_tiebreakers
        self.full_records = tally(series, teams)

    def resolve(self, group: tuple[str, ...], index: int) -> list[tuple[str, ...]]:
        """Split `group` with the tiebreakers from `index` on, best subgroup first.

        Args:
            group: Teams tied so far.
            index: Position of the next tiebreaker to apply.

        Returns:
            Subgroups in ranking order; a subgroup of several teams is a tie the tiebreakers cannot settle.
        """
        if len(group) == 1 or index == len(self.tiebreakers):
            return [group]
        values = self._values(self.tiebreakers[index], group)
        if values is None:
            return [group]  # goals unknown: this tiebreaker (and the ones after it) cannot be applied yet
        splits: list[tuple[str, ...]] = []
        for value in sorted(set(values.values()), reverse=True):
            splits.extend(self.resolve(tuple(team for team in group if values[team] == value), index + 1))
        return splits

    def _values(self, tiebreaker: Tiebreaker, group: tuple[str, ...]) -> dict[str, TiebreakValue] | None:
        """Return each team's tiebreaker value within `group`, or None when one of them needs unknown goals."""
        records = self._records(tiebreaker, group)
        values = {team: _value(tiebreaker, records[team], self.manual.get(team, 0)) for team in group}
        known = {team: value for team, value in values.items() if value is not None}
        return known if len(known) == len(values) else None

    def _records(self, tiebreaker: Tiebreaker, group: tuple[str, ...]) -> dict[str, TeamRecord]:
        """Return the records a tiebreaker is computed on: the whole league, or the series within `group` (`ml.`)."""
        if tiebreaker.context == CONTEXT_FULL or tiebreaker.name == TIEBREAKER_MANUAL:
            return self.full_records
        members = set(group)
        return tally((item for item in self.series if item.team_1 in members and item.team_2 in members), group)
