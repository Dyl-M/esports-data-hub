"""Data models for rating predictions: past matches as read from the LPDB history, and the lineups playing them."""

# Standard library
import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass

# Local
from .constants import TEAM_KINDS

# === Match history ===


@dataclass(frozen=True)
class Player:
    """A player of a team in a match.

    Attributes:
        page: Liquipedia page of the player (`name` in LPDB), the player's identity across teams.
        name: Display name.
        flag: Nationality as Liquipedia shows it (e.g. `France`); empty when unknown.
    """

    page: str
    name: str
    flag: str = ''

    def __post_init__(self) -> None:
        """Validate the player."""
        if not self.page:
            raise ValueError('player page cannot be empty')


@dataclass(frozen=True)
class Opponent:
    """A team in a match.

    Attributes:
        name: Team page name (aliases are not applied here).
        template: Team template of the period (e.g. `karmine corp 2024`); empty when unknown.
        score: Games won; for a walkover, 1 for the winner and 0 for the forfeiting team.
        players: Roster of the match, in LPDB order; empty when not recorded.
        national: Whether it is a national team (a flag as its team icon).
    """

    name: str
    template: str
    score: int
    players: tuple[Player, ...] = ()
    national: bool = False

    def __post_init__(self) -> None:
        """Validate the opponent."""
        if not self.name:
            raise ValueError('opponent name cannot be empty')
        if self.score < 0:
            raise ValueError(f'score must be >= 0, got {self.score}')


@dataclass(frozen=True)
class PlayedMatch:
    """A finished match (series) between two teams.

    Attributes:
        match_id: LPDB `match2id`, unique per wiki.
        page: Liquipedia page holding the match (a tournament page or one of its subpages).
        played_at: Start time, in UTC.
        tier: Liquipedia tier of the tournament (`1`, `2`, ...).
        lan: Whether it was played on LAN; None when LPDB does not say.
        opponents: The two teams.
        games: Goals of each decided game, `(opponent_1, opponent_2)`; empty for a walkover or unrecorded games.
        walkover: Whether the match was decided without playing (forfeit, disqualification).
    """

    match_id: str
    page: str
    played_at: dt.datetime
    tier: str
    lan: bool | None
    opponents: tuple[Opponent, Opponent]
    games: tuple[tuple[int, int], ...] = ()
    walkover: bool = False

    def __post_init__(self) -> None:
        """Validate the match."""
        if self.played_at.tzinfo is None:
            raise ValueError('played_at must be timezone-aware (UTC)')
        first, second = self.opponents
        if first.name == second.name:
            raise ValueError(f'a team cannot play itself: {first.name!r}')
        if first.score == second.score:
            raise ValueError(f'a finished match has a winner: {first.score}-{second.score} in {self.match_id}')

    @property
    def winner(self) -> int:
        """Index of the winning opponent (0 or 1)."""
        scores = [opponent.score for opponent in self.opponents]
        return scores.index(max(scores))


# === Lineups ===


@dataclass(frozen=True)
class Lineup:
    """A team name and the rosters it played with while keeping a core of players.

    Attributes:
        lineup_id: Index of the lineup, in order of first match.
        name: Team name.
        kind: `club`, `national` or `college`, as when it first played.
        first_played: Start time of its first match, in UTC.
        predecessor: Lineup it took its core from (a rename, a new name or a concurrent squad); None for a new core.
    """

    lineup_id: int
    name: str
    kind: str
    first_played: dt.datetime
    predecessor: int | None = None

    def __post_init__(self) -> None:
        """Validate the lineup."""
        if self.kind not in TEAM_KINDS:
            raise ValueError(f'kind must be one of {TEAM_KINDS}, got {self.kind!r}')
        if self.predecessor is not None and not 0 <= self.predecessor < self.lineup_id:
            raise ValueError(f'predecessor must be an earlier lineup, got {self.predecessor} for {self.lineup_id}')


@dataclass(frozen=True)
class Lineups:
    """Lineup identity over a match history.

    Attributes:
        lineups: Every lineup, indexed by `lineup_id`.
        sides: Lineup ids of the two opponents of each match, by `match_id`.
    """

    lineups: tuple[Lineup, ...]
    sides: Mapping[str, tuple[int, int]]

    def of(self, match: PlayedMatch) -> tuple[Lineup, Lineup]:
        """Return the lineups of the two opponents of a match.

        Args:
            match: A match of the history the lineups were identified on.

        Returns:
            The lineups of the first and second opponents.
        """
        first, second = self.sides[match.match_id]
        return self.lineups[first], self.lineups[second]
