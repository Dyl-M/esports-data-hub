"""Liquipedia API (LPDB v3) access shared by the subpackages: API key, client and the queries they use.

The client is `liquipydia.LiquipediaClient`; code takes it typed against the Protocols below, so tests can pass a fake
one serving recorded payloads.
"""

# Standard library
import json
import os
from collections.abc import Iterator, Sequence
from typing import Any, Protocol

# Third-party
from liquipydia import ApiResponse, LiquipediaClient, Match

# Local
from . import paths
from .exceptions import ConfigurationError

# === Constants ===

LPDB_API_KEY_ENV = 'LPDB_API_KEY'  # takes precedence over paths.TOKENS_JSON
LPDB_APP_NAME = 'esports-data-hub'  # User-Agent application name
LPDB_MAX_PAGE_SIZE = 1000  # the API's own limit per request

# Opponent statuses of a match or game
STATUS_SCORED = 'S'  # regular (scored) result
STATUS_WALKOVER_WIN = 'W'
STATUS_WALKOVER_LOSSES = frozenset({'FF', 'DQ', 'L'})  # forfeit, disqualified, loss
STATUS_WALKOVERS = STATUS_WALKOVER_LOSSES | {STATUS_WALKOVER_WIN}
GAME_NOT_PLAYED = 'notplayed'  # game status of a game left unplayed (series already decided)

# === Protocols ===


class LpdbStandingsTables(Protocol):
    """The `/standingstable` queries used here (liquipydia's `client.standings_tables`)."""

    def list(self, wiki: str, *, pagename: str, order: str | None = None) -> ApiResponse:
        """Return one page of the standings tables stored on pagename."""
        ...


class LpdbMatches(Protocol):
    """The `/match` queries used here (liquipydia's `client.matches`)."""

    def paginate(
        self,
        wiki: str,
        *,
        conditions: str | None = None,
        query: str | None = None,
        order: str | None = None,
        page_size: int = 50,
    ) -> Iterator[dict[str, Any]]:
        """Iterate over every match meeting conditions (only the query fields when given), page by page."""
        ...


class LpdbTournaments(Protocol):
    """The `/tournament` queries used here (liquipydia's `client.tournaments`)."""

    def list(self, wiki: str, *, pagename: str, query: str | None = None) -> ApiResponse:
        """Return the tournament stored on pagename."""
        ...


class LpdbTeamTemplates(Protocol):
    """The `/teamtemplate` queries used here (liquipydia's `client.team_templates`)."""

    def get(self, wiki: str, template: str, *, date: str | None = None) -> ApiResponse:
        """Return the team template named template."""
        ...


class LpdbMatchClient(Protocol):
    """An LPDB client used for its matches only (e.g. the match history); `liquipydia.LiquipediaClient` satisfies it."""

    @property
    def matches(self) -> LpdbMatches:
        """Match queries."""
        ...


class LpdbClient(LpdbMatchClient, Protocol):
    """The part of an LPDB client used here; `liquipydia.LiquipediaClient` satisfies it."""

    @property
    def standings_tables(self) -> LpdbStandingsTables:
        """Standings table queries."""
        ...

    @property
    def tournaments(self) -> LpdbTournaments:
        """Tournament queries."""
        ...

    @property
    def team_templates(self) -> LpdbTeamTemplates:
        """Team template queries."""
        ...


# === Functions ===


def page_condition(pagename: str) -> str:
    """Return the LPDB condition selecting the records stored on a page, e.g. `[[pagename::NEO/Coupe_de_France]]`.

    Args:
        pagename: Liquipedia page name, with underscores.

    Returns:
        The condition.
    """
    return f'[[pagename::{pagename}]]'


def first_wins_walkover(statuses: Sequence[str]) -> bool:
    """Whether the first opponent wins a walkover: it has a walkover win or the second one forfeited.

    Args:
        statuses: Statuses of the two opponents (at least one of `STATUS_WALKOVERS`).

    Returns:
        True when the first opponent wins.
    """
    return statuses[0] == STATUS_WALKOVER_WIN or statuses[1] in STATUS_WALKOVER_LOSSES


def decided_goals(match: Match) -> tuple[tuple[int, int], ...]:
    """Return the goals of each decided game of a match, in game order.

    A game is decided when it has a winner, was played and both opponents have a regular score.

    Args:
        match: The LPDB match.

    Returns:
        `(opponent_1, opponent_2)` goals per decided game.
    """
    return tuple(_game_goals(game) for game in match.match2games or [] if _is_decided(game))


def _is_decided(game: dict[str, Any]) -> bool:
    """Whether a game has a winner and regular scores for both opponents."""
    opponents = game.get('opponents') or []
    return (
        bool(game.get('winner'))
        and game.get('status') != GAME_NOT_PLAYED
        and len(game.get('scores') or []) == 2
        and all(isinstance(opponent, dict) and opponent.get('status') == STATUS_SCORED for opponent in opponents)
    )


def _game_goals(game: dict[str, Any]) -> tuple[int, int]:
    """Goals of a decided game, `(opponent_1, opponent_2)`."""
    first, second = game['scores']
    return int(first), int(second)


def load_api_key() -> str:
    """Return the LPDB API key, from `LPDB_API_KEY` or the `lpdb` field of `paths.TOKENS_JSON`.

    Returns:
        The API key.

    Raises:
        ConfigurationError: If neither holds a key.
    """
    key = os.environ.get(LPDB_API_KEY_ENV)
    if key:
        return key
    tokens = paths.TOKENS_JSON
    try:
        key = json.loads(tokens.read_text(encoding='utf-8')).get('lpdb')
    except FileNotFoundError:
        key = None
    except (json.JSONDecodeError, AttributeError) as error:
        raise ConfigurationError(f'Invalid token file: {tokens}', file_path=str(tokens)) from error
    if not key:
        raise ConfigurationError(
            f'No LPDB API key: set {LPDB_API_KEY_ENV} or add "lpdb" to {tokens}', file_path=str(tokens)
        )
    return str(key)


def open_client(*, max_retries: int = 3, retry_backoff_factor: float = 1.0) -> LiquipediaClient:
    """Open a liquipydia client with the key from `load_api_key()`; use it as a context manager.

    Args:
        max_retries: Retries after a rate-limited (HTTP 429) request.
        retry_backoff_factor: Base of the exponential wait between those retries, in seconds (capped by liquipydia).

    Returns:
        The client.

    Raises:
        ConfigurationError: If no API key is available.
    """
    return LiquipediaClient(
        LPDB_APP_NAME, api_key=load_api_key(), max_retries=max_retries, retry_backoff_factor=retry_backoff_factor
    )
