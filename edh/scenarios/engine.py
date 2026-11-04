"""Scenario evaluation: exhaustive enumeration or Monte Carlo sampling of the remaining series.

Every reachable final score of a remaining series is equally likely (a Bo5 at 0-0 ends 3-0, 3-1, 3-2, 0-3, 1-3 or
2-3 with probability 1/6 each), so exhaustive shares are plain shares of scenarios and Monte Carlo estimates them.
"""

# Standard library
import math
from collections.abc import Iterator, Sequence
from dataclasses import replace
from itertools import product

# Third-party
import numpy as np

# Local
from ..exceptions import ScenarioError
from .constants import (
    DEFAULT_SAMPLES,
    MAX_EXHAUSTIVE_SCENARIOS,
    METHOD_AUTO,
    METHOD_EXHAUSTIVE,
    METHOD_MONTE_CARLO,
    METHODS,
    SAMPLE_BATCH,
)
from .models import LeagueConfig, QualificationSlot, RankGroup, ScenarioReport, Series, TeamOutlook
from .outcomes import reachable_scores
from .standings import rank


def count_scenarios(series: Sequence[Series], wins_needed: int) -> int:
    """Count the scenarios left, i.e. the combinations of reachable final scores of the unfinished series.

    Args:
        series: League series.
        wins_needed: Games needed to win a series.

    Returns:
        Number of scenarios (1 when every series is finished).
    """
    return math.prod(len(reachable_scores(item, wins_needed)) for item in series if not item.finished)


def choose_method(method: str, scenarios: int, max_exhaustive: int | None = None) -> str:
    """Resolve the evaluation method.

    Args:
        method: `auto`, `exhaustive` or `montecarlo`.
        scenarios: Number of scenarios left.
        max_exhaustive: Largest scenario count enumerated exhaustively (default: `MAX_EXHAUSTIVE_SCENARIOS`).

    Returns:
        `exhaustive` or `montecarlo`.

    Raises:
        ScenarioError: If the method is unknown, or exhaustive is requested above `max_exhaustive`.
    """
    limit = MAX_EXHAUSTIVE_SCENARIOS if max_exhaustive is None else max_exhaustive
    if method not in METHODS:
        raise ScenarioError(f'Unknown method {method!r} (expected one of {", ".join(METHODS)})')
    if method == METHOD_AUTO:
        return METHOD_EXHAUSTIVE if scenarios <= limit else METHOD_MONTE_CARLO
    if method == METHOD_EXHAUSTIVE and scenarios > limit:
        raise ScenarioError(f'{scenarios:,} scenarios exceed the exhaustive limit ({limit:,}): use Monte Carlo')
    return method


class _Accumulator:
    """Running totals of rank groups over the evaluated scenarios."""

    def __init__(self, teams: Sequence[str], slots: Sequence[QualificationSlot]) -> None:
        """Start empty totals for `teams` and qualification `slots`."""
        self.slots = slots
        self.scenarios = 0
        self.rank_shares = {team: [0.0] * len(teams) for team in teams}
        self.qualified = {team: dict.fromkeys((slot.label for slot in slots), 0) for team in teams}
        self.undecided = {team: dict.fromkeys((slot.label for slot in slots), 0) for team in teams}
        self.unresolved = dict.fromkeys(teams, 0)

    def add(self, groups: Sequence[RankGroup]) -> None:
        """Count one scenario's ranking."""
        self.scenarios += 1
        for group in groups:
            share = 1 / len(group.teams)
            for team in group.teams:
                for position in range(group.rank, group.last + 1):
                    self.rank_shares[team][position - 1] += share
                self.unresolved[team] += not group.resolved
                for slot in self.slots:
                    if slot.first <= group.rank and group.last <= slot.last:
                        self.qualified[team][slot.label] += 1
                    elif group.rank <= slot.last and slot.first <= group.last:
                        self.undecided[team][slot.label] += 1

    def outlooks(self) -> tuple[TeamOutlook, ...]:
        """Turn the totals into per-team shares, with projected ranks, in projected standings order."""
        total = self.scenarios
        outlooks = [
            TeamOutlook(
                team=team,
                rank_shares=tuple(value / total for value in shares),
                qualified={label: count / total for label, count in self.qualified[team].items()},
                undecided={label: count / total for label, count in self.undecided[team].items()},
                unresolved=self.unresolved[team] / total,
            )
            for team, shares in self.rank_shares.items()
        ]
        projected = project_ranks(outlooks)

        return tuple(
            sorted(
                (replace(outlook, projected_rank=projected[outlook.team]) for outlook in outlooks),
                key=lambda outlook: projected[outlook.team],
            )
        )


def project_ranks(outlooks: Sequence[TeamOutlook]) -> dict[str, int]:
    """Give each rank to a single team, following the likelihood of each team / rank pairing.

    Pairings are taken from the most to the least likely, and kept when both the team and the rank are still free:
    the team most likely to hold a rank gets it, and a team that loses its most likely rank falls back to its next
    most likely free one. Equal shares go to the better rank, then to the team with the better expected rank.

    Args:
        outlooks: Per-team outlook (one rank per team).

    Returns:
        Projected rank (1-based) of each team.
    """
    pairings = sorted(
        (
            (-share, position, outlook.expected_rank, outlook.team)
            for outlook in outlooks
            for position, share in enumerate(outlook.rank_shares, start=1)
        )
    )
    projected: dict[str, int] = {}
    taken: set[int] = set()
    for _, position, _, team in pairings:
        if team not in projected and position not in taken:
            projected[team] = position
            taken.add(position)
    return projected


def evaluate(
    config: LeagueConfig,
    series: Sequence[Series],
    *,
    method: str = METHOD_AUTO,
    samples: int = DEFAULT_SAMPLES,
    seed: int | None = None,
) -> ScenarioReport:
    """Evaluate every scenario (or a sample of them) of the remaining series and aggregate the rankings.

    Args:
        config: League format.
        series: League series, played and remaining; team aliases are applied here (manual tiebreakers included).
        method: `auto`, `exhaustive` or `montecarlo`.
        samples: Monte Carlo sample count.
        seed: Monte Carlo random seed, for reproducible runs.

    Returns:
        The per-team outlook.

    Raises:
        ScenarioError: If there are no series, a score is impossible, the method is invalid, or `samples` is below 1.
    """
    if not series:
        raise ScenarioError('No series to evaluate')
    if samples < 1:
        raise ScenarioError(f'samples must be >= 1, got {samples}')

    aliases = config.team_aliases
    current = [item.renamed(aliases) for item in series]
    manual = {aliases.get(team, team): points for team, points in config.manual_tiebreakers.items()}
    teams = sorted({team for item in current for team in item.teams})
    played = [item for item in current if item.finished]
    # Every way each pending series can end, built once: scenarios only pick among them
    endings = [
        [_complete(item, score) for score in reachable_scores(item, config.wins_needed)]
        for item in current
        if not item.finished
    ]
    resolved_method = choose_method(method, math.prod(map(len, endings)))

    accumulator = _Accumulator(teams, config.qualification)
    for completed in _scenarios(endings, resolved_method, samples, seed):
        accumulator.add(rank([*played, *completed], teams, config.tiebreakers, manual))

    return ScenarioReport(
        league=config.name,
        method=resolved_method,
        scenarios=accumulator.scenarios,
        remaining=len(endings),
        teams=accumulator.outlooks(),
    )


def _scenarios(
    endings: Sequence[Sequence[Series]], method: str, samples: int, seed: int | None
) -> Iterator[tuple[Series, ...]]:
    """Yield the completed pending series, one tuple per scenario."""
    if method == METHOD_EXHAUSTIVE:
        yield from product(*endings)
        return
    rng = np.random.default_rng(seed)
    counts = np.array([len(options) for options in endings], dtype=np.int64)
    for start in range(0, samples, SAMPLE_BATCH):
        # One draw per (sample, pending series): an index into that series' equally likely endings
        picks = rng.integers(0, counts, size=(min(SAMPLE_BATCH, samples - start), len(endings)))
        for row in picks.tolist():
            yield tuple(options[index] for options, index in zip(endings, row, strict=True))


def _complete(series: Series, score: tuple[int, int]) -> Series:
    """Return the series finished with `score`; the goals of the simulated games are unknown."""
    return replace(series, score_1=score[0], score_2=score[1], finished=True, goals=None)
