"""Data models for league play scenarios."""

# Standard library
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Self

# Local
from ..exceptions import ScenarioError
from .constants import (
    CONTEXT_FULL,
    CONTEXT_MINI_LEAGUE,
    CONTEXTS,
    DEFAULT_SAMPLES,
    EXACT_METHODS,
    METHOD_AUTO,
    TIEBREAKER_MANUAL,
    TIEBREAKERS,
)

HEX_COLOR = re.compile(r'#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})')

# === Results ===


@dataclass(frozen=True)
class Series:
    """A series (best-of-N) between two teams, played, in progress or upcoming.

    Attributes:
        round: Round (day / week) number, starting at 1.
        team_1: First team name.
        team_2: Second team name.
        score_1: Games won by `team_1` so far.
        score_2: Games won by `team_2` so far.
        finished: Whether the series is over.
        goals: Goals per game, `(team_1, team_2)`, in game order; None when unknown (e.g. a simulated series).
    """

    round: int
    team_1: str
    team_2: str
    score_1: int = 0
    score_2: int = 0
    finished: bool = False
    goals: tuple[tuple[int, int], ...] | None = None

    def __post_init__(self) -> None:
        """Validate the series."""
        if self.round < 1:
            raise ValueError(f'round must be >= 1, got {self.round}')
        if not self.team_1 or not self.team_2:
            raise ValueError('team names cannot be empty')
        if self.team_1 == self.team_2:
            raise ValueError(f'a team cannot play itself: {self.team_1!r}')
        if self.score_1 < 0 or self.score_2 < 0:
            raise ValueError(f'scores must be >= 0, got {self.score_1}-{self.score_2}')

    @property
    def teams(self) -> tuple[str, str]:
        """Both team names."""
        return self.team_1, self.team_2

    def renamed(self, aliases: dict[str, str]) -> Self:
        """Return the series with team names mapped through `aliases` (old name -> current name).

        Args:
            aliases: Old name to current name mapping.

        Returns:
            The series with current team names.
        """
        return replace(self, team_1=aliases.get(self.team_1, self.team_1), team_2=aliases.get(self.team_2, self.team_2))


# === Event ===


@dataclass(frozen=True)
class EventInfo:
    """Event details shown on visuals, beyond the league format.

    Attributes:
        organizers: Tournament organizers, in Liquipedia order (empty when unknown).
    """

    organizers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Theme:
    """Colors of the outlook card, tuned per competition (league.json `theme`); the neutral palette by default.

    Qualified / eliminated / tiebreak colors are fixed across themes. Every color is `#rrggbb`, or `#rrggbbaa` for
    a translucent one.

    Attributes:
        background: Card background.
        surface: Empty rank cells and bar tracks.
        line: Row separators.
        text: Main text.
        muted: Labels and secondary text.
        heat: Rank strip color (probability of a rank).
        accent: Title highlight.
        glow: Radial glow in the top right corner (usually translucent).
    """

    background: str = '#11141b'
    surface: str = '#1a1e28'
    line: str = '#2a3040'
    text: str = '#eceff5'
    muted: str = '#8a93a6'
    heat: str = '#6f8cff'
    accent: str = '#6f8cff'
    glow: str = '#6f8cff1a'

    def __post_init__(self) -> None:
        """Validate the colors."""
        for name, value in asdict(self).items():
            if not HEX_COLOR.fullmatch(value):
                raise ValueError(f'theme color {name} must be #rrggbb or #rrggbbaa, got {value!r}')


# === League format ===


@dataclass(frozen=True)
class Tiebreaker:
    """One tiebreaker step, e.g. `ml.gamediff` (game differential among the tied teams).

    Attributes:
        name: Liquipedia tiebreaker identifier (e.g. `gamediff`).
        context: `full` (whole league) or `ml` (series between the tied teams only).
    """

    name: str
    context: str

    def __post_init__(self) -> None:
        """Validate the tiebreaker."""
        if self.name not in TIEBREAKERS:
            raise ValueError(f'Unsupported tiebreaker {self.name!r} (supported: {", ".join(sorted(TIEBREAKERS))})')
        if self.context not in CONTEXTS:
            raise ValueError(f'Unsupported tiebreaker context {self.context!r} (supported: {", ".join(CONTEXTS)})')
        if self.name == TIEBREAKER_MANUAL and self.context == CONTEXT_MINI_LEAGUE:
            raise ValueError('manual tiebreaker cannot be scoped to a mini-league')

    @classmethod
    def parse(cls, value: str) -> Self:
        """Parse a Liquipedia identifier, `name` or `context.name`.

        Args:
            value: Identifier such as `gamediff` or `ml.gamediff`.

        Returns:
            The parsed tiebreaker.
        """
        context, _, name = value.rpartition('.')
        return cls(name=name, context=context or CONTEXT_FULL)

    def __str__(self) -> str:
        """Return the Liquipedia identifier."""
        return self.name if self.context == CONTEXT_FULL else f'{self.context}.{self.name}'


@dataclass(frozen=True)
class QualificationSlot:
    """A range of final ranks leading to the same outcome (e.g. ranks 1-4 go to the playoffs).

    Attributes:
        label: Outcome name.
        first: Best rank of the range (1-based).
        last: Worst rank of the range, inclusive.
    """

    label: str
    first: int
    last: int

    def __post_init__(self) -> None:
        """Validate the rank range."""
        if not self.label:
            raise ValueError('qualification label cannot be empty')
        if not 1 <= self.first <= self.last:
            raise ValueError(f'invalid rank range for {self.label!r}: {self.first}-{self.last}')


@dataclass(frozen=True)
class LeagueConfig:
    """Format of a league, read from `_data/<league>/league.json`.

    Attributes:
        slug: League folder name in `_data/`.
        name: Display name.
        wiki: Liquipedia wiki (e.g. `rocketleague`).
        pagename: Liquipedia page holding the league matches (underscores, no namespace).
        best_of: Series length.
        tiebreakers: Ordered tiebreakers applied to rank teams.
        qualification: Rank ranges and their outcome.
        walkover_games: Games credited to the winner of a walkover.
        team_aliases: Old team name to current name.
        manual_tiebreakers: Manual tiebreak points per team (higher ranks first), 0 when absent.
        theme: Colors of the league's outlook card.
    """

    slug: str
    name: str
    wiki: str
    pagename: str
    best_of: int
    tiebreakers: tuple[Tiebreaker, ...]
    qualification: tuple[QualificationSlot, ...] = ()
    walkover_games: int = 0
    team_aliases: dict[str, str] = field(default_factory=dict)
    manual_tiebreakers: dict[str, int] = field(default_factory=dict)
    theme: Theme = field(default_factory=Theme)

    def __post_init__(self) -> None:
        """Validate the league format."""
        if self.best_of < 1 or self.best_of % 2 == 0:
            raise ValueError(f'best_of must be a positive odd number, got {self.best_of}')
        if not self.tiebreakers:
            raise ValueError('at least one tiebreaker is required')
        if self.walkover_games < 0:
            raise ValueError(f'walkover_games must be >= 0, got {self.walkover_games}')

    @property
    def wins_needed(self) -> int:
        """Games needed to win a series."""
        return self.best_of // 2 + 1


# === Scenario outcomes ===


@dataclass(frozen=True)
class EvaluationOptions:
    """How to evaluate the scenarios.

    Attributes:
        method: `auto`, `exhaustive`, `winnersfirst` or `montecarlo` (checked when the method is chosen).
        samples: Monte Carlo sample count.
        seed: Monte Carlo random seed, for reproducible runs.
        workers: Worker processes (None: one per CPU; 1: evaluate in the calling process).
    """

    method: str = METHOD_AUTO
    samples: int = DEFAULT_SAMPLES
    seed: int | None = None
    workers: int | None = None

    def __post_init__(self) -> None:
        """Validate the counts."""
        if self.samples < 1:
            raise ScenarioError(f'samples must be >= 1, got {self.samples}')
        if self.workers is not None and self.workers < 1:
            raise ScenarioError(f'workers must be >= 1, got {self.workers}')


@dataclass(frozen=True)
class RankGroup:
    """Teams sharing a position after the tiebreakers.

    Attributes:
        rank: Best rank covered by the group (1-based).
        teams: Teams in the group.
    """

    rank: int
    teams: tuple[str, ...]

    @property
    def resolved(self) -> bool:
        """Whether the group is a single team (the tiebreakers separated it from everyone)."""
        return len(self.teams) == 1

    @property
    def last(self) -> int:
        """Worst rank covered by the group."""
        return self.rank + len(self.teams) - 1


@dataclass(frozen=True)
class TeamOutlook:
    """Aggregated outcome of one team over every evaluated scenario.

    Attributes:
        team: Team name.
        rank_shares: Share of scenarios ending at each rank (index 0 = rank 1); a tie the tiebreakers cannot
            settle is spread evenly over the ranks it covers.
        qualified: Share of scenarios qualifying per slot label, ties straddling the slot excluded.
        undecided: Share of scenarios per slot label where the team sits in a tie straddling the slot (settled by
            goal difference or manual tiebreak once known).
        unresolved: Share of scenarios where the team ends in a tie the tiebreakers cannot settle.
        projected_rank: Rank in the projected standings, where each rank goes to a single team (see
            `engine.project_ranks`); None until projected.

    """

    team: str
    rank_shares: tuple[float, ...]
    qualified: dict[str, float]
    undecided: dict[str, float]
    unresolved: float
    projected_rank: int | None = None

    @property
    def expected_rank(self) -> float:
        """Mean final rank."""
        return sum(rank * share for rank, share in enumerate(self.rank_shares, start=1))


@dataclass(frozen=True)
class ScenarioReport:
    """Outcome of a scenario evaluation.

    Attributes:
        league: League display name.
        method: `exhaustive`, `winnersfirst` or `montecarlo`.
        scenarios: Number of scenarios covered (all of them for exact methods, or the samples).
        remaining: Number of series left to play (in progress included).
        teams: Per-team outlook, in projected standings order.
    """

    league: str
    method: str
    scenarios: int
    remaining: int
    teams: tuple[TeamOutlook, ...]

    @property
    def exact(self) -> bool:
        """Whether shares are exact (every scenario evaluated) rather than estimated."""
        return self.method in EXACT_METHODS

    @property
    def slot_labels(self) -> list[str]:
        """Qualification slot labels, in league.json order."""
        return list(self.teams[0].qualified) if self.teams else []

    @property
    def rank_count(self) -> int:
        """Number of ranks (one per team)."""
        return len(self.teams)
