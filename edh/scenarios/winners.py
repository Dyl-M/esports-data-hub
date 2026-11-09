"""Winners-first exact evaluation: exact shares far beyond the exhaustive enumeration limit.

When the leading tiebreakers depend on series winners only (`matchscore`, `matchwins`, `matchdiff`, their `ml.`
versions, `manual`), the ranking is built in two stages:

1. Enumerate the **winners** of the remaining series (at most 2 options each, instead of 2k scores), each combination
   weighted by the probability of its winners: the share of the series' reachable scores that each winner takes (1/2
   at 0-0, 3/4 for the leader at 2-0 in a Bo5). The leading tiebreakers split the teams into groups no margin can
   change.
2. Only a group still tied needs margins, and only those of the series involving its teams: it is resolved with the
   remaining tiebreakers over every combination of those margins (each reachable score of a given winner is equally
   likely), after summing the combinations into distinct game statistics. The result depends on the group and on the
   winners of those series only, so it is cached and shared by every winner combination that reaches it.

A team's shares only depend on the group it falls in, so the margins of the series outside a tied group sum out, and
two tied groups sharing a series don't need to be resolved jointly. Shares are the exhaustive engine's exactly (up to
float rounding); goal tiebreakers stay unresolved as there.
"""

# Standard library
import math
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import product

# Third-party
import numpy as np

# Local
from ..exceptions import ScenarioError
from .constants import CONTEXT_FULL, MARGIN_FREE_TIEBREAKERS
from .models import LeagueConfig, RankGroup, Series, Tiebreaker
from .outcomes import Score, reachable_scores
from .standings import RecordsProvider, TeamRecord, TieResolver, rank_groups, tally

Group = tuple[str, ...]
WeightedGroup = tuple[RankGroup, float]
Resolution = tuple[Group, ...]  # subgroups of a tied group, in ranking order
State = tuple[int, ...]  # summed game statistics of the simulated series involving a tied group
Move = tuple[tuple[int, int], ...]  # (state slot, increment) pairs added by one final score

# Game statistics a state keeps, from the game tiebreakers left to apply: none, the difference only, the games won
# only, or both games won and lost
_KEEP_NONE, _KEEP_DIFF, _KEEP_WINS, _KEEP_BOTH = range(4)
# Statistics compared team against team for each of them (games won, games lost, game difference)
_STAT_WON, _STAT_LOST, _STAT_DIFF = 'won', 'lost', 'diff'
_COMPARED = {
    _KEEP_NONE: (),
    _KEEP_DIFF: (_STAT_DIFF,),
    _KEEP_WINS: (_STAT_WON,),
    _KEEP_BOTH: (_STAT_WON, _STAT_LOST, _STAT_DIFF),
}
_MAX_CODED_ROWS = 39  # comparison patterns coded as base-3 integers up to 39 signs (3^39 < 2^63), compared whole above


def margin_free_prefix(tiebreakers: Sequence[Tiebreaker]) -> int:
    """Count the leading tiebreakers that depend on series winners only.

    Args:
        tiebreakers: Ordered tiebreakers.

    Returns:
        Number of leading series-winner (or manual) tiebreakers; 0 when the winners-first method doesn't apply.
    """
    count = 0
    for tiebreaker in tiebreakers:
        if tiebreaker.name not in MARGIN_FREE_TIEBREAKERS:
            break
        count += 1
    return count


@dataclass(frozen=True)
class _Outcome:
    """One possible winner of a remaining series.

    Attributes:
        winner: Winning team.
        loser: Losing team.
        probability: Share of the series' reachable final scores this winner takes.
        scores: Those final scores, as `(winner games, loser games)`.
    """

    winner: str
    loser: str
    probability: float
    scores: tuple[Score, ...]


def _outcomes(series: Series, wins_needed: int) -> tuple[_Outcome, ...]:
    """List the possible winners of a series (a single one when it is already won)."""
    scores = reachable_scores(series, wins_needed)
    outcomes = []
    for winner, loser, side in ((series.team_1, series.team_2, 0), (series.team_2, series.team_1, 1)):
        won = tuple((score[side], score[1 - side]) for score in scores if score[side] > score[1 - side])
        if won:
            outcomes.append(_Outcome(winner, loser, len(won) / len(scores), won))
    return tuple(outcomes)


def count_winner_combinations(series: Sequence[Series], wins_needed: int) -> int:
    """Count the combinations of winners of the unfinished series.

    Args:
        series: League series.
        wins_needed: Games needed to win a series.

    Returns:
        Number of winner combinations (at most 2 per unfinished series; 1 when every series is finished).
    """
    return math.prod(len(_outcomes(item, wins_needed)) for item in series if not item.finished)


def _keep(names: set[str]) -> int:
    """Return the game statistics needed by the game tiebreakers among `names`."""
    if 'gamescore' in names or {'gamediff', 'gamewins'} <= names:
        return _KEEP_BOTH
    if 'gamediff' in names:
        return _KEEP_DIFF
    if 'gamewins' in names:
        return _KEEP_WINS
    return _KEEP_NONE


def _project(keep: int, won: int, lost: int) -> tuple[int, int]:
    """Reduce games won / lost to what the tiebreakers read, as increments of a record's games won / lost.

    Only the difference (or the games won) is kept when that is all the tiebreakers read, so that scenarios with
    the same value merge into one state.
    """
    if keep == _KEEP_BOTH:
        return won, lost
    if keep == _KEEP_DIFF:
        return won - lost, 0
    if keep == _KEEP_WINS:
        return won, 0
    return 0, 0


def _spread(states: Mapping[State, float], moves: Sequence[Move]) -> dict[State, float]:
    """Spread each state over equally likely moves, merging the states reached by several paths."""
    share = 1 / len(moves)
    merged: defaultdict[State, float] = defaultdict(float)
    for state, weight in states.items():
        for move in moves:
            values = list(state)
            for slot, increment in move:
                values[slot] += increment
            merged[tuple(values)] += weight * share
    return dict(merged)


class _ComparisonGrid:
    """Every joint outcome of a tied group's external series, compared team against team.

    Built once per tied group and winners; each state of the group's internal series only shifts the comparisons.
    """

    def __init__(self, keep: int, external: Sequence[Mapping[State, float]]) -> None:
        """Lay out the joint outcomes (cells) and the pairwise differences of the statistics the tiebreakers read.

        Args:
            keep: Game statistics read by the full-league tiebreakers left.
            external: Per team, the distribution of the games won / lost (as kept) added by its external series.
        """
        size = len(external)
        values = [np.array(list(dist), dtype=np.int64).reshape(-1, 2) for dist in external]
        joint = np.ones(())
        for dist in external:
            joint = np.multiply.outer(joint, np.fromiter(dist.values(), dtype=np.float64, count=len(dist)))
        self.joint = joint.ravel()
        cells = np.indices(joint.shape).reshape(size, -1)
        self.won = np.stack([values[team][cells[team], 0] for team in range(size)])
        self.lost = np.stack([values[team][cells[team], 1] for team in range(size)])
        self.first, self.second = np.triu_indices(size, 1)
        self.compared = _COMPARED[keep]
        rows = [self._pairwise(stat, self.won, self.lost) for stat in self.compared]
        self.rows = np.concatenate(rows) if rows else np.zeros((0, self.joint.size), dtype=np.int64)
        count = self.rows.shape[0]
        self.powers = 3 ** np.arange(count, dtype=np.int64) if count <= _MAX_CODED_ROWS else None

    def _pairwise(self, stat: str, won: np.ndarray, lost: np.ndarray) -> np.ndarray:
        """Return a statistic's difference for each pair of teams (first axis), along the following axes."""
        values = {_STAT_WON: won, _STAT_LOST: lost}.get(stat, won - lost)
        difference: np.ndarray = values[self.first] - values[self.second]
        return difference

    def classes(
        self, bases: Sequence[tuple[int, int]]
    ) -> Iterator[tuple[int | tuple[int, ...], list[tuple[int, int]], float]]:
        """Split the joint outcomes by comparison pattern, the teams starting from `bases`.

        Args:
            bases: Per team, the games won / lost (as kept) before the external series.

        Yields:
            Per comparison pattern: a key identifying it (the signs of the compared statistics for each pair of
            teams), the totals of one outcome showing that pattern, and the pattern's probability.
        """
        won = np.array([base[0] for base in bases], dtype=np.int64)
        lost = np.array([base[1] for base in bases], dtype=np.int64)
        shifts = [self._pairwise(stat, won, lost) for stat in self.compared]
        shift = np.concatenate(shifts) if shifts else np.zeros(0, dtype=np.int64)
        signs = np.sign(self.rows + shift[:, np.newaxis])
        keys: Sequence[int | tuple[int, ...]]
        if self.powers is not None:
            # One base-3 code per cell: a 1-D unique is much faster than a column-wise one
            codes, firsts, inverse = np.unique((signs + 1).T @ self.powers, return_index=True, return_inverse=True)
            keys = codes.tolist()
        else:
            patterns, firsts, inverse = np.unique(signs, axis=1, return_index=True, return_inverse=True)
            keys = [tuple(column) for column in patterns.T.tolist()]
        shares = np.bincount(inverse.ravel(), weights=self.joint, minlength=len(keys))
        for key, cell, share in zip(keys, firsts.tolist(), shares.tolist(), strict=True):
            totals = [
                (base_won + int(self.won[team, cell]), base_lost + int(self.lost[team, cell]))
                for team, (base_won, base_lost) in enumerate(bases)
            ]
            yield key, totals, share


@dataclass(frozen=True)
class _TiedCase:
    """A tied group whose series winners are fixed, as its margins are resolved.

    Attributes:
        group: Tied teams.
        internal: Remaining series between two of them.
        offsets: State slot of the games won / lost by each ordered pair of them in their internal series.
        matches: Every team's series won / lost, the winners being fixed.
    """

    group: Group
    internal: tuple[_Outcome, ...]
    offsets: dict[tuple[str, str], int]
    matches: dict[str, list[int]]


@dataclass(frozen=True)
class _Read:
    """Node of the split cache: the resolver reads the mini-league records of `subgroup` next."""

    subgroup: Group


def _fingerprint(records: Mapping[str, TeamRecord]) -> tuple[tuple[int | bool, ...], ...]:
    """Return the records as a hashable key, in their order."""
    return tuple(
        (
            record.match_wins,
            record.match_losses,
            record.game_wins,
            record.game_losses,
            record.round_wins,
            record.round_losses,
            record.rounds_known,
        )
        for record in records.values()
    )


class WinnersFirst:
    """Weighted rankings of every winner combination of the remaining series."""

    def __init__(
        self,
        played: Sequence[Series],
        pending: Sequence[Series],
        config: LeagueConfig,
        manual_tiebreakers: Mapping[str, int],
    ) -> None:
        """Prepare the played records and the possible winners of each remaining series.

        Args:
            played: Finished series, team aliases applied.
            pending: Unfinished series (upcoming or in progress), team aliases applied.
            config: League format; its first tiebreaker must depend on series winners only.
            manual_tiebreakers: Manual tiebreak points per team (0 when absent), team aliases applied.

        Raises:
            ScenarioError: If the first tiebreaker depends on game or goal margins.
        """
        prefix = margin_free_prefix(config.tiebreakers)
        if not prefix:
            names = ', '.join(str(item) for item in config.tiebreakers)  # never empty (LeagueConfig)
            raise ScenarioError(f'The winners-first method needs a first tiebreaker on series winners (got: {names})')
        self.prefix = prefix
        self.teams = tuple(sorted({team for item in [*played, *pending] for team in item.teams}))
        self.tiebreakers = config.tiebreakers
        self.manual = manual_tiebreakers
        self.played = tuple(played)
        self.outcomes = [_outcomes(item, config.wins_needed) for item in pending]
        self.base = tally(self.played, self.teams)
        self.simulated = {team for item in pending for team in item.teams}  # their goals become unknown
        rest = self.tiebreakers[prefix:]
        self.keep_full = _keep({item.name for item in rest if item.context == CONTEXT_FULL})
        self.keep_mini = _keep({item.name for item in rest if item.context != CONTEXT_FULL})
        self._involved: dict[Group, tuple[int, ...]] = {}
        self._mini_played: dict[Group, dict[str, TeamRecord]] = {}
        self._resolutions: dict[tuple[Group, tuple[int, ...]], list[tuple[Resolution, float]]] = {}
        self._splits: dict[tuple[object, ...], Resolution | _Read] = {}  # decision tree, see `_cached_split`

    def combinations(self, prefix: Sequence[int] = ()) -> Iterator[tuple[float, list[WeightedGroup]]]:
        """Yield each winner combination's probability and its rank groups, each with its probability.

        Groups of a team tied on the leading tiebreakers come with the share of the margin combinations giving
        that split; the groups of a combination cover every team, for a total weight of its probability per team.

        Args:
            prefix: Winners of the first remaining series (indices into their possible winners), to enumerate only
                the combinations starting with them (a chunk of a parallel run).

        Yields:
            `(probability, [(rank group, probability), ...])` per winner combination.
        """
        fixed = len(prefix)
        choices = [(pick,) for pick in prefix] + [range(len(options)) for options in self.outcomes[fixed:]]
        for picks in product(*choices):
            chosen = [options[pick] for options, pick in zip(self.outcomes, picks, strict=True)]
            probability = math.prod(outcome.probability for outcome in chosen)
            weighted: list[WeightedGroup] = []
            position = 1
            for group in self._split_by_winners(chosen):
                if len(group) == 1:
                    weighted.append((RankGroup(rank=position, teams=group), probability))
                else:
                    for resolution, share in self._resolve(group, picks, chosen):
                        weighted.extend((item, probability * share) for item in rank_groups(resolution, position))
                position += len(group)
            yield probability, weighted

    def _split_by_winners(self, chosen: Sequence[_Outcome]) -> list[Group]:
        """Split the teams with the leading (series-winner) tiebreakers."""
        full = {team: TeamRecord(record.match_wins, record.match_losses) for team, record in self.base.items()}
        for outcome in chosen:
            full[outcome.winner].match_wins += 1
            full[outcome.loser].match_losses += 1

        def mini(group: Group) -> Mapping[str, TeamRecord]:
            """Return the series records of `group` over the series between its teams."""
            records = {
                team: TeamRecord(record.match_wins, record.match_losses)
                for team, record in self._played_between(group).items()
            }
            for decided in chosen:
                if decided.winner in records and decided.loser in records:
                    records[decided.winner].match_wins += 1
                    records[decided.loser].match_losses += 1
            return records

        return TieResolver(self.tiebreakers[: self.prefix], self.manual, full, mini).resolve(self.teams, 0)

    def _resolve(
        self, group: Group, picks: Sequence[int], chosen: Sequence[_Outcome]
    ) -> list[tuple[Resolution, float]]:
        """Return the splits of a tied group with their probabilities, cached per winners of its series."""
        involved = self._involved_in(group)
        key = (group, tuple(picks[index] for index in involved))
        if key not in self._resolutions:
            self._resolutions[key] = self._resolve_margins(group, [chosen[index] for index in involved])
        return self._resolutions[key]

    def _involved_in(self, group: Group) -> tuple[int, ...]:
        """Return the indices of the remaining series involving a team of `group`."""
        if group not in self._involved:
            members = set(group)
            self._involved[group] = tuple(
                index
                for index, options in enumerate(self.outcomes)
                if options[0].winner in members or options[0].loser in members
            )
        return self._involved[group]

    def _played_between(self, group: Group) -> dict[str, TeamRecord]:
        """Return the records of `group` over the finished series between its teams (cached, never mutated)."""
        if group not in self._mini_played:
            self._mini_played[group] = tally(self.played, group)  # tally skips series with outsiders
        return self._mini_played[group]

    def _resolve_margins(self, group: Group, involved: Sequence[_Outcome]) -> list[tuple[Resolution, float]]:
        """Resolve a tied group over every margin combination of its series, the winners being fixed.

        Series between two teams of the group (internal) are enumerated: the state holds, per team, the games won /
        lost in them, then per ordered pair of teams, the games won / lost in the series between the two (for the
        `ml.` tiebreakers); margin combinations giving the same state are merged. Given that state, each team's games
        against teams outside the group are independent of the other teams': they are summed into one distribution
        per team, and only how the teams' full-league statistics then compare matters (`_ComparisonGrid`).
        """
        index = {team: position for position, team in enumerate(group)}
        internal = tuple(outcome for outcome in involved if outcome.winner in index and outcome.loser in index)
        offsets: dict[tuple[str, str], int] = {}
        for outcome in internal:
            for pair in ((outcome.winner, outcome.loser), (outcome.loser, outcome.winner)):
                offsets.setdefault(pair, 2 * len(group) + 2 * len(offsets))
        states: dict[State, float] = {(0,) * (2 * len(group) + 2 * len(offsets)): 1.0}
        for outcome in internal:
            states = _spread(states, [self._move(outcome, score, index, offsets) for score in outcome.scores])

        case = _TiedCase(group, internal, offsets, self._series_records(involved))
        grid = _ComparisonGrid(self.keep_full, self._external(index, involved))
        # The external series only see the full-league part of an internal state: group the states by it
        by_full: defaultdict[State, list[tuple[State, float]]] = defaultdict(list)
        for state, weight in states.items():
            by_full[state[: 2 * len(group)]].append((state, weight))
        resolutions: defaultdict[Resolution, float] = defaultdict(float)
        for full_part, variants in by_full.items():
            self._resolve_variants(case, grid, full_part, variants, resolutions)
        return list(resolutions.items())

    def _series_records(self, involved: Sequence[_Outcome]) -> dict[str, list[int]]:
        """Return every team's series won / lost, the winners of the series involving a tied group being fixed."""
        matches = {team: [record.match_wins, record.match_losses] for team, record in self.base.items()}
        for outcome in involved:
            matches[outcome.winner][0] += 1
            matches[outcome.loser][1] += 1
        return matches

    def _resolve_variants(
        self,
        case: _TiedCase,
        grid: _ComparisonGrid,
        full_part: State,
        variants: Sequence[tuple[State, float]],
        resolutions: defaultdict[Resolution, float],
    ) -> None:
        """Add the splits of the internal states sharing one full-league part, over every external outcome."""
        group = case.group
        bases = [
            (
                self.base[team].game_wins + full_part[2 * position],
                self.base[team].game_losses + full_part[2 * position + 1],
            )
            for position, team in enumerate(group)
        ]
        total = sum(weight for _, weight in variants)
        group_matches = tuple(tuple(case.matches[team]) for team in group)
        for pattern, totals, share in grid.classes(bases):
            full = self._full_records(group, totals, case.matches)
            root = (group, group_matches, pattern)
            node = self._splits.get(root)
            if node is not None and not isinstance(node, _Read):
                resolutions[node] += total * share  # no `ml.` tiebreaker read: same split for every variant
                continue
            for state, weight in variants:
                split = self._cached_split(group, root, full, self._mini_records(state, case.internal, case.offsets))
                resolutions[split] += weight * share

    def _external(self, index: Mapping[str, int], involved: Sequence[_Outcome]) -> list[dict[State, float]]:
        """Return, per team of a tied group, the distribution of games won / lost (as kept) in its external series."""
        start: State = (0, 0)  # no game won or lost yet
        external = [{start: 1.0} for _ in index]
        for outcome in involved:
            if (outcome.winner in index) == (outcome.loser in index):
                continue  # internal series
            side = 0 if outcome.winner in index else 1
            team = outcome.winner if side == 0 else outcome.loser
            moves = [
                tuple(enumerate(_project(self.keep_full, score[side], score[1 - side]))) for score in outcome.scores
            ]
            external[index[team]] = _spread(external[index[team]], moves)
        return external

    def _cached_split(
        self, group: Group, root: tuple[object, ...], full: Mapping[str, TeamRecord], mini: RecordsProvider
    ) -> Resolution:
        """Resolve a tied group for one outcome, through the cache of splits.

        The cache is a decision tree: a split depends on what the resolver reads, i.e. the full-league records (the
        `root` key: the group, its series record and the comparison pattern of its game statistics), then the
        mini-league records of each subgroup it asks for, in a fixed order. A node holds either the split or the
        next subgroup read, whose records extend the key.

        Args:
            group: Tied group.
            root: Key of the full-league records.
            full: Full-league records of the group's teams.
            mini: Mini-league records provider for this outcome.

        Returns:
            The split of the group, in ranking order.
        """
        key = root
        while isinstance(node := self._splits.get(key), _Read):
            key = (*key, _fingerprint(mini(node.subgroup)))
        if node is not None:
            return node

        reads: list[tuple[Group, tuple[object, ...]]] = []

        def watched(asked: Group) -> Mapping[str, TeamRecord]:
            """Return the mini-league records of the `asked` subgroup, noting that the resolver read them."""
            records = mini(asked)
            reads.append((asked, _fingerprint(records)))
            return records

        split = tuple(TieResolver(self.tiebreakers, self.manual, full, watched).resolve(group, self.prefix))
        key = root
        for subgroup, fingerprint in reads:
            self._splits.setdefault(key, _Read(subgroup))
            key = (*key, fingerprint)
        self._splits[key] = split
        return split

    def _move(
        self, outcome: _Outcome, score: Score, index: Mapping[str, int], offsets: Mapping[tuple[str, str], int]
    ) -> Move:
        """Return the state increments of one final score of a series between two teams of the group."""
        won, lost = score
        increments: list[tuple[int, int]] = []
        for team, opponent, games in (
            (outcome.winner, outcome.loser, score),
            (outcome.loser, outcome.winner, (lost, won)),
        ):
            games_won, games_lost = _project(self.keep_full, *games)
            increments += [(2 * index[team], games_won), (2 * index[team] + 1, games_lost)]
            slot = offsets[team, opponent]
            games_won, games_lost = _project(self.keep_mini, *games)
            increments += [(slot, games_won), (slot + 1, games_lost)]
        return tuple(item for item in increments if item[1])

    def _full_records(
        self, group: Group, totals: Sequence[tuple[int, int]], matches: Mapping[str, list[int]]
    ) -> dict[str, TeamRecord]:
        """Build the full-league records of the group's teams from their games won / lost (as kept)."""
        records = {}
        for team, (game_wins, game_losses) in zip(group, totals, strict=True):
            base = self.base[team]
            records[team] = TeamRecord(
                match_wins=matches[team][0],
                match_losses=matches[team][1],
                game_wins=game_wins,
                game_losses=game_losses,
                round_wins=base.round_wins,
                round_losses=base.round_losses,
                rounds_known=base.rounds_known and team not in self.simulated,
            )
        return records

    def _mini_records(
        self, state: State, involved: Sequence[_Outcome], offsets: Mapping[tuple[str, str], int]
    ) -> RecordsProvider:
        """Build the mini-league records provider of one state."""

        def records(subgroup: Group) -> Mapping[str, TeamRecord]:
            """Return the records of `subgroup` over the series between its teams, played and simulated."""
            members = set(subgroup)
            result = {}
            for team, base in self._played_between(subgroup).items():
                record = TeamRecord(
                    base.match_wins,
                    base.match_losses,
                    base.game_wins,
                    base.game_losses,
                    base.round_wins,
                    base.round_losses,
                    base.rounds_known,
                )
                for opponent in members - {team}:
                    slot = offsets.get((team, opponent))
                    if slot is not None:
                        record.game_wins += state[slot]
                        record.game_losses += state[slot + 1]
                        record.rounds_known = False
                result[team] = record
            for outcome in involved:
                if outcome.winner in members and outcome.loser in members:
                    result[outcome.winner].match_wins += 1
                    result[outcome.loser].match_losses += 1
            return result

        return records
