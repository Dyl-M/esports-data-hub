"""Liquipedia results through `liquipydia` (LPDB v3).

The league's standings table (`/standingstable`) lists its match ids per round, which keeps other stages (e.g.
playoffs on the same page) out. The matches themselves come from `/match`: series score per opponent (-1 until
played), per-game goals in `match2games`. The table also records the tiebreakers Liquipedia applies; a mismatch with
`league.json` is logged, since `league.json` stays the reference.
"""

# Standard library
import json
import logging
import os
from typing import Any

# Third-party
from liquipydia import LiquipediaClient, LiquipediaError, Match, StandingsTable

# Local
from ... import paths
from ...exceptions import ConfigurationError, DataSourceError
from ..constants import (
    CONTEXT_FULL,
    LPDB_API_KEY_ENV,
    LPDB_APP_NAME,
    LPDB_PAGE_SIZE,
    STATUS_SCORED,
    STATUS_WALKOVER_LOSSES,
    STATUS_WALKOVER_WIN,
    STATUS_WALKOVERS,
    TIEBREAKER_DISQUALIFIED,
)
from ..models import LeagueConfig, Series

logger = logging.getLogger(__name__)


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


class LiquipediaSource:
    """Fetch league series from Liquipedia."""

    def __init__(self, client: LiquipediaClient | None = None) -> None:
        """Use `client`, or create one from `load_api_key()` on each fetch.

        Args:
            client: An open liquipydia client (tests pass a fake one).
        """
        self.client = client

    def fetch(self, config: LeagueConfig) -> list[Series]:
        """Return the league series, in round order.

        Args:
            config: League format (`wiki` and `pagename` locate the league).

        Returns:
            Every series of the league's standings table, finished or not.

        Raises:
            DataSourceError: If the API fails or returns no usable standings table.
            ConfigurationError: If no API key is available.
        """
        if self.client is not None:
            return _fetch(self.client, config)
        with LiquipediaClient(LPDB_APP_NAME, api_key=load_api_key()) as client:
            return _fetch(client, config)


def _fetch(client: LiquipediaClient, config: LeagueConfig) -> list[Series]:
    """Query the standings table and its matches, translating liquipydia errors into DataSourceError."""
    try:
        tables = client.standings_tables.list(config.wiki, pagename=config.pagename, order='standingsindex ASC')
        records = client.matches.paginate(
            config.wiki, pagename=config.pagename, order='date ASC', page_size=LPDB_PAGE_SIZE
        )
        matches = {match.match2id: match for match in (Match.model_validate(record) for record in records)}
    except LiquipediaError as error:
        raise DataSourceError(f'Liquipedia request failed for {config.pagename}: {error}') from error

    if not tables.result:
        raise DataSourceError(f'No standings table on {config.pagename}')
    table = StandingsTable.model_validate(tables.result[0])
    _check_tiebreakers(table, config)

    series = []
    for round_number, match_id in _table_matches(table):
        match = matches.get(match_id)
        if match is None:
            raise DataSourceError(f'Match {match_id} of the standings table is missing from {config.pagename}')
        series.append(to_series(match, round_number, config))
    return series


def _numbered(mapping: dict[str, Any]) -> list[Any]:
    """Values of an LPDB dict keyed by `'1'`, `'2'`, ..., in numeric key order."""
    return [value for _, value in sorted(mapping.items(), key=lambda item: int(item[0]))]


def _table_matches(table: StandingsTable) -> list[tuple[int, str]]:
    """List `(round, match id)` pairs of a standings table, in round then match order."""
    rounds: dict[str, Any] = (table.extradata or {}).get('rounds') or {}
    pairs = [
        (int(key), match_id)
        for key, entry in sorted(rounds.items(), key=lambda item: int(item[0]))
        for match_id in _numbered(entry.get('matches') or {})
    ]
    if not pairs:
        raise DataSourceError(f'Standings table of {table.parent} lists no rounds')
    return pairs


def to_series(match: Match, round_number: int, config: LeagueConfig) -> Series:
    """Convert an LPDB match into a series.

    Args:
        match: The LPDB match.
        round_number: Round of the match in the standings table.
        config: League format (walkover games).

    Returns:
        The series. `goals` lists the goals of each decided game: empty for a walkover, None for a finished series
        whose games are not all scored (goal tiebreakers then treat it as unknown).

    Raises:
        DataSourceError: If the match does not have two named opponents.
    """
    first, second = _opponents(match)
    finished = bool(match.finished)
    statuses = (first.get('status') or '', second.get('status') or '')
    if any(status in STATUS_WALKOVERS for status in statuses):
        score_1, score_2 = _walkover_scores(statuses, config.walkover_games)
        return Series(round_number, first['name'], second['name'], score_1, score_2, finished, ())

    score_1, score_2 = _score(first), _score(second)
    goals = _goals(match, expected_games=score_1 + score_2 if finished else None)
    return Series(round_number, first['name'], second['name'], score_1, score_2, finished, goals)


def _opponents(match: Match) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the two opponents of a match, raising DataSourceError unless both are named (not TBD)."""
    opponents: list[dict[str, Any]] = list(match.match2opponents or [])
    if len(opponents) != 2 or not all(opponent.get('name') for opponent in opponents):
        raise DataSourceError(f'Match {match.match2id} does not have two named opponents')
    first, second = ({**opponent, 'name': str(opponent['name'])} for opponent in opponents)
    return first, second


def _walkover_scores(statuses: tuple[str, str], walkover_games: int) -> tuple[int, int]:
    """Series score of a walkover: `walkover_games` to the winner, 0 to the forfeiting team."""
    first_wins = statuses[0] == STATUS_WALKOVER_WIN or statuses[1] in STATUS_WALKOVER_LOSSES
    return (walkover_games, 0) if first_wins else (0, walkover_games)


def _score(opponent: dict[str, Any]) -> int:
    """Games won by an opponent; LPDB stores -1 until the series starts."""
    return max(int(opponent.get('score') or 0), 0)


def _goals(match: Match, *, expected_games: int | None) -> tuple[tuple[int, int], ...] | None:
    """Goals of each decided game, or None when fewer games are scored than `expected_games` (partial data)."""
    goals = tuple(_game_goals(game) for game in match.match2games or [] if _is_decided(game))
    if expected_games is not None and len(goals) != expected_games:
        return None
    return goals


def _is_decided(game: dict[str, Any]) -> bool:
    """Whether a game has a winner and regular scores for both opponents."""
    opponents = game.get('opponents') or []
    return (
        bool(game.get('winner'))
        and game.get('status') != 'notplayed'
        and len(game.get('scores') or []) == 2
        and all(isinstance(opponent, dict) and opponent.get('status') == STATUS_SCORED for opponent in opponents)
    )


def _game_goals(game: dict[str, Any]) -> tuple[int, int]:
    """Goals of a decided game, `(team_1, team_2)`."""
    first, second = game['scores']
    return int(first), int(second)


def _check_tiebreakers(table: StandingsTable, config: LeagueConfig) -> None:
    """Log a warning when Liquipedia's tiebreakers differ from `league.json`.

    Liquipedia prefixes full-league tiebreakers with `full.`, always applies `disqualified` first and appends a
    second `manual`; these are normalized away before comparing.
    """
    stats: dict[str, Any] = (table.extradata or {}).get('additionalStats') or {}
    identifiers = (
        str(stat.get('id', '')).removeprefix(f'{CONTEXT_FULL}.') for stat in _numbered(stats) if stat.get('tiebreaker')
    )
    liquipedia = list(dict.fromkeys(item for item in identifiers if item != TIEBREAKER_DISQUALIFIED))
    configured = [str(tiebreaker) for tiebreaker in config.tiebreakers]
    if liquipedia and liquipedia != configured:
        logger.warning('Liquipedia tiebreakers %s differ from league.json %s', liquipedia, configured)
