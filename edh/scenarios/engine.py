"""Scenario evaluation: exhaustive enumeration, winners-first enumeration or Monte Carlo sampling.

Every reachable final score of a remaining series is equally likely (a Bo5 at 0-0 ends 3-0, 3-1, 3-2, 0-3, 1-3 or
2-3 with probability 1/6 each), so exact shares are plain shares of scenarios and Monte Carlo estimates them. The
winners-first method (`winners.py`) gives the exhaustive shares without enumerating every scenario.
"""

# Standard library
import math
import os
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from itertools import product

# Third-party
import numpy as np

# Local
from ..exceptions import ScenarioError
from .constants import (
    MAX_EXHAUSTIVE_SCENARIOS,
    MAX_WINNER_COMBINATIONS,
    METHOD_AUTO,
    METHOD_EXHAUSTIVE,
    METHOD_MONTE_CARLO,
    METHOD_WINNERS_FIRST,
    METHODS,
    PARALLEL_CHUNKS,
    PARALLEL_MIN_WORK,
    PARALLEL_WINNERS_COST,
    PROJECTION_DIGITS,
    SAMPLE_BATCH,
)
from .models import EvaluationOptions, LeagueConfig, QualificationSlot, RankGroup, ScenarioReport, Series, TeamOutlook
from .outcomes import reachable_scores
from .standings import Standings
from .winners import WeightedGroup, WinnersFirst, count_winner_combinations, margin_free_prefix


def count_scenarios(series: Sequence[Series], wins_needed: int) -> int:
    """Count the scenarios left, i.e. the combinations of reachable final scores of the unfinished series.

    Args:
        series: League series.
        wins_needed: Games needed to win a series.

    Returns:
        Number of scenarios (1 when every series is finished).
    """
    return math.prod(len(reachable_scores(item, wins_needed)) for item in series if not item.finished)


def choose_method(
    method: str,
    scenarios: int,
    combinations: int | None = None,
    *,
    max_exhaustive: int | None = None,
    max_combinations: int | None = None,
) -> str:
    """Resolve the evaluation method.

    `auto` picks the first method that fits: exhaustive, then winners-first, then Monte Carlo.

    Args:
        method: `auto`, `exhaustive`, `winnersfirst` or `montecarlo`.
        scenarios: Number of scenarios left.
        combinations: Number of winner combinations left, or None when the winners-first method doesn't apply (the
            first tiebreaker depends on game or goal margins).
        max_exhaustive: Largest scenario count enumerated exhaustively (default: `MAX_EXHAUSTIVE_SCENARIOS`).
        max_combinations: Largest winner combination count enumerated (default: `MAX_WINNER_COMBINATIONS`).

    Returns:
        `exhaustive`, `winnersfirst` or `montecarlo`.

    Raises:
        ScenarioError: If the method is unknown, or an exact method is requested where it doesn't apply or above
            its limit.
    """
    limit = MAX_EXHAUSTIVE_SCENARIOS if max_exhaustive is None else max_exhaustive
    combination_limit = MAX_WINNER_COMBINATIONS if max_combinations is None else max_combinations
    if method not in METHODS:
        raise ScenarioError(f'Unknown method {method!r} (expected one of {", ".join(METHODS)})')
    if method == METHOD_AUTO:
        return _auto_method(scenarios, combinations, limit, combination_limit)
    if method == METHOD_EXHAUSTIVE and scenarios > limit:
        raise ScenarioError(f'{scenarios:,} scenarios exceed the exhaustive limit ({limit:,}): use Monte Carlo')
    if method == METHOD_WINNERS_FIRST:
        _check_winners_first(combinations, combination_limit)
    return method


def _auto_method(scenarios: int, combinations: int | None, limit: int, combination_limit: int) -> str:
    """Pick the first method that fits: exhaustive, then winners-first, then Monte Carlo."""
    if scenarios <= limit:
        return METHOD_EXHAUSTIVE
    if combinations is not None and combinations <= combination_limit:
        return METHOD_WINNERS_FIRST
    return METHOD_MONTE_CARLO


def _check_winners_first(combinations: int | None, combination_limit: int) -> None:
    """Raise ScenarioError if the winners-first method doesn't apply or exceeds its limit."""
    if combinations is None:
        raise ScenarioError('The winners-first method needs a first tiebreaker on series winners')
    if combinations > combination_limit:
        raise ScenarioError(
            f'{combinations:,} winner combinations exceed the winners-first limit ({combination_limit:,}): '
            'use Monte Carlo'
        )


class _Accumulator:
    """Running totals of rank groups over the evaluated scenarios, each group with its probability weight."""

    def __init__(self, teams: Sequence[str], slots: Sequence[QualificationSlot]) -> None:
        """Start empty totals for `teams` and qualification `slots`."""
        self.slots = slots
        self.total = 0.0
        self.rank_shares = {team: [0.0] * len(teams) for team in teams}
        self.qualified = {team: dict.fromkeys((slot.label for slot in slots), 0.0) for team in teams}
        self.undecided = {team: dict.fromkeys((slot.label for slot in slots), 0.0) for team in teams}
        self.unresolved = dict.fromkeys(teams, 0.0)

    def merge(self, other: '_Accumulator') -> None:
        """Add the totals of another accumulator (same teams and slots), e.g. a chunk evaluated in a worker."""
        self.total += other.total
        for team, shares in other.rank_shares.items():
            self.rank_shares[team] = [
                mine + theirs for mine, theirs in zip(self.rank_shares[team], shares, strict=True)
            ]
            self.unresolved[team] += other.unresolved[team]
            for label in self.qualified[team]:
                self.qualified[team][label] += other.qualified[team][label]
                self.undecided[team][label] += other.undecided[team][label]

    def add(self, groups: Sequence[RankGroup]) -> None:
        """Count one scenario's ranking."""
        self.total += 1
        for group in groups:
            self._add_group(group, 1)

    def add_weighted(self, probability: float, groups: Sequence[WeightedGroup]) -> None:
        """Count a set of scenarios of total `probability`, each rank group weighted by its own probability."""
        self.total += probability
        for group, weight in groups:
            self._add_group(group, weight)

    def _add_group(self, group: RankGroup, weight: float) -> None:
        """Count one rank group with a weight."""
        share = weight / len(group.teams)
        for team in group.teams:
            for position in range(group.rank, group.last + 1):
                self.rank_shares[team][position - 1] += share
            if not group.resolved:
                self.unresolved[team] += weight
            for slot in self.slots:
                if slot.first <= group.rank and group.last <= slot.last:
                    self.qualified[team][slot.label] += weight
                elif group.rank <= slot.last and slot.first <= group.last:
                    self.undecided[team][slot.label] += weight

    def outlooks(self) -> tuple[TeamOutlook, ...]:
        """Turn the totals into per-team shares, with projected ranks, in projected standings order."""
        total = self.total
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
            (-round(share, PROJECTION_DIGITS), position, round(outlook.expected_rank, PROJECTION_DIGITS), outlook.team)
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
    config: LeagueConfig, series: Sequence[Series], options: EvaluationOptions | None = None
) -> ScenarioReport:
    """Evaluate every scenario (or a sample of them) of the remaining series and aggregate the rankings.

    The work is cut into fixed chunks (independent of the number of workers) evaluated in worker processes and merged
    in order, so the result is the same whatever the number of workers. Small evaluations run in this process.

    Args:
        config: League format.
        series: League series, played and remaining; team aliases are applied here (manual tiebreakers included).
        options: Method, Monte Carlo samples / seed and worker count (defaults: `EvaluationOptions()`).

    Returns:
        The per-team outlook.

    Raises:
        ScenarioError: If there are no series, a score is impossible, or the method is invalid.
    """
    options = options or EvaluationOptions()
    if not series:
        raise ScenarioError('No series to evaluate')
    job = _prepare(config, series, options.method)
    chunks, workers = _plan(job, options)
    accumulator = _Accumulator(job.teams, config.qualification)
    for result in _run_chunks(chunks, workers):
        accumulator.merge(result)

    return ScenarioReport(
        league=config.name,
        method=job.method,
        scenarios=options.samples if job.method == METHOD_MONTE_CARLO else job.scenarios,
        remaining=len(job.pending),
        teams=accumulator.outlooks(),
    )


def _prepare(config: LeagueConfig, series: Sequence[Series], method: str) -> '_Job':
    """Apply the team aliases (manual tiebreakers included), split played / pending series and resolve the method."""
    aliases = config.team_aliases
    current = [item.renamed(aliases) for item in series]
    pending = tuple(item for item in current if not item.finished)
    scenarios = count_scenarios(pending, config.wins_needed)
    combinations = (
        count_winner_combinations(pending, config.wins_needed) if margin_free_prefix(config.tiebreakers) else None
    )
    return _Job(
        config=config,
        teams=tuple(sorted({team for item in current for team in item.teams})),
        played=tuple(item for item in current if item.finished),
        pending=pending,
        manual={aliases.get(team, team): points for team, points in config.manual_tiebreakers.items()},
        method=choose_method(method, scenarios, combinations),
        scenarios=scenarios,
        combinations=combinations,
    )


def _plan(job: '_Job', options: EvaluationOptions) -> tuple[list['_Chunk'], int]:
    """Cut the evaluation into chunks and pick the worker count (1, i.e. in-process, for small evaluations)."""
    if job.method == METHOD_MONTE_CARLO:
        chunks = _sample_chunks(job, options.samples, options.seed)
        work = options.samples
    else:
        chunks = _prefix_chunks(job)
        work = job.scenarios if job.method == METHOD_EXHAUSTIVE else (job.combinations or 0) * PARALLEL_WINNERS_COST
    if work < PARALLEL_MIN_WORK or len(chunks) == 1:
        return chunks, 1
    return chunks, options.workers or os.cpu_count() or 1


@dataclass(frozen=True)
class _Job:
    """What every chunk of an evaluation shares (team aliases applied, method resolved)."""

    config: LeagueConfig
    teams: tuple[str, ...]
    played: tuple[Series, ...]
    pending: tuple[Series, ...]
    manual: dict[str, int]
    method: str
    scenarios: int = 0  # scenarios left
    combinations: int | None = None  # winner combinations left (None: winners-first doesn't apply)


@dataclass(frozen=True)
class _Chunk:
    """An independent slice of an evaluation, evaluated in a worker process.

    Attributes:
        job: The evaluation.
        prefix: Exhaustive: ending indices of the first pending series; winners-first: their winner indices.
        samples: Monte Carlo: samples to draw.
        seed: Monte Carlo: the chunk's own seed (spawned from the run's seed).
    """

    job: _Job
    prefix: tuple[int, ...] = ()
    samples: int = 0
    seed: np.random.SeedSequence | None = None


def _prefix_chunks(job: _Job) -> list[_Chunk]:
    """Cut an exact enumeration by the outcomes of its first pending series, into about `PARALLEL_CHUNKS` chunks."""
    wins_needed = job.config.wins_needed
    if job.method == METHOD_WINNERS_FIRST:
        counts = [count_winner_combinations([item], wins_needed) for item in job.pending]
    else:
        counts = [len(reachable_scores(item, wins_needed)) for item in job.pending]
    fixed = 0
    while fixed < len(counts) and math.prod(counts[:fixed]) < PARALLEL_CHUNKS:
        fixed += 1
    return [_Chunk(job, prefix) for prefix in product(*(range(count) for count in counts[:fixed]))]


def _sample_chunks(job: _Job, samples: int, seed: int | None) -> list[_Chunk]:
    """Cut a Monte Carlo run into chunks of `SAMPLE_BATCH` samples, each with its own seed spawned from `seed`."""
    sizes = [min(SAMPLE_BATCH, samples - start) for start in range(0, samples, SAMPLE_BATCH)]
    seeds = np.random.SeedSequence(seed).spawn(len(sizes))
    return [_Chunk(job, samples=size, seed=chunk_seed) for size, chunk_seed in zip(sizes, seeds, strict=True)]


def _run_chunks(chunks: Sequence[_Chunk], workers: int) -> Iterator[_Accumulator]:
    """Evaluate chunks in this process (one worker) or in a process pool, yielding their totals in chunk order."""
    if workers == 1:
        yield from map(_run_chunk, chunks)
        return
    with ProcessPoolExecutor(max_workers=min(workers, len(chunks))) as pool:
        yield from pool.map(_run_chunk, chunks)


def _run_chunk(chunk: _Chunk) -> _Accumulator:
    """Evaluate one chunk (a module-level function, so that worker processes can import it)."""
    job = chunk.job
    config = job.config
    accumulator = _Accumulator(job.teams, config.qualification)
    if job.method == METHOD_WINNERS_FIRST:
        winners = WinnersFirst(job.played, job.pending, config, job.manual)
        for probability, groups in winners.combinations(chunk.prefix):
            accumulator.add_weighted(probability, groups)
        return accumulator
    # Every way each pending series can end, built once: scenarios only pick among them
    endings = [[_complete(item, score) for score in reachable_scores(item, config.wins_needed)] for item in job.pending]
    if job.method == METHOD_EXHAUSTIVE:
        fixed = len(chunk.prefix)
        decided = [[endings[position][index]] for position, index in enumerate(chunk.prefix)]
        scenarios: Iterable[Sequence[Series]] = product(*decided, *endings[fixed:])
    else:
        scenarios = _samples(endings, chunk.samples, np.random.default_rng(chunk.seed))
    standings = Standings(job.played, job.teams, config.tiebreakers, job.manual)  # played series tallied once
    for completed in scenarios:
        accumulator.add(standings.rank(completed))
    return accumulator


def _samples(endings: Sequence[Sequence[Series]], samples: int, rng: np.random.Generator) -> Iterator[list[Series]]:
    """Yield `samples` random scenarios: one equally likely ending drawn per pending series."""
    counts = np.array([len(options) for options in endings], dtype=np.int64)
    picks = rng.integers(0, counts, size=(samples, len(endings)))
    for row in picks.tolist():
        yield [options[index] for options, index in zip(endings, row, strict=True)]


def _complete(series: Series, score: tuple[int, int]) -> Series:
    """Return the series finished with `score`; the goals of the simulated games are unknown."""
    return replace(series, score_1=score[0], score_2=score[1], finished=True, goals=None)
