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


RecordsProvider = Callable[[tuple[str, ...]], Mapping[str, TeamRecord]]


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
    resolver = TieResolver(tiebreakers, manual_tiebreakers or {}, tally(series, teams), mini_league_records(series))
    return rank_groups(resolver.resolve(tuple(sorted(teams)), 0))


def rank_groups(groups: Iterable[tuple[str, ...]], first: int = 1) -> list[RankGroup]:
    """Number consecutive groups of teams with their ranks.

    Args:
        groups: Groups of teams in ranking order.
        first: Rank of the first group.

    Returns:
        The groups with their first rank; a group of k teams covers k ranks.
    """
    ranked: list[RankGroup] = []
    position = first
    for group in groups:
        ranked.append(RankGroup(rank=position, teams=group))
        position += len(group)
    return ranked


def mini_league_records(series: Sequence[Series]) -> RecordsProvider:
    """Build the mini-league records provider of a set of series.

    Args:
        series: League series; only finished ones count.

    Returns:
        A function giving, for a group of teams, their records over the series played between them only.
    """

    def records(group: tuple[str, ...]) -> Mapping[str, TeamRecord]:
        """Return the records of `group` over the series between its teams."""
        members = set(group)
        return tally((item for item in series if item.team_1 in members and item.team_2 in members), group)

    return records


class TieResolver:
    """Split tied groups with the tiebreakers, following Liquipedia's resolution order.

    Records are given rather than tallied here, so that callers can build them from partial information (e.g.
    series winners only, or game statistics shared by many scenarios at once).
    """

    def __init__(
        self,
        tiebreakers: Sequence[Tiebreaker],
        manual_tiebreakers: Mapping[str, int],
        full_records: Mapping[str, TeamRecord],
        mini_records: RecordsProvider,
    ) -> None:
        """Store the tiebreakers and the records they are computed on.

        Args:
            tiebreakers: Ordered tiebreakers.
            manual_tiebreakers: Manual tiebreak points per team (0 when absent).
            full_records: Record of each team over the whole league.
            mini_records: Records of a group's teams over the series between them only (`ml.` tiebreakers).
        """
        self.tiebreakers = tiebreakers
        self.manual = manual_tiebreakers
        self.full_records = full_records
        self.mini_records = mini_records

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
        if tiebreaker.context == CONTEXT_FULL or tiebreaker.name == TIEBREAKER_MANUAL:
            records = self.full_records
        else:
            records = self.mini_records(group)
        values = {team: _value(tiebreaker, records[team], self.manual.get(team, 0)) for team in group}
        known = {team: value for team, value in values.items() if value is not None}
        return known if len(known) == len(values) else None
