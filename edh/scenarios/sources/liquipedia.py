"""Liquipedia results through `liquipydia` (LPDB v3).

The league's standings table (`/standingstable`) lists its match ids per round, which keeps other stages (e.g.
playoffs on the same page) out. The matches themselves come from `/match`: series score per opponent (-1 until
played), per-game goals in `match2games`. The table also records the tiebreakers Liquipedia applies; a mismatch with
`league.json` is logged, since `league.json` stays the reference. The page's tournament record (`/tournament`) gives the
event details shown on visuals (organizers, competition logo); the team logos come from the team template each match
opponent uses (`/teamtemplate`, the logo of the period, e.g. `karmine corp 2024`).
"""

# Standard library
import json
import logging
import os
import re
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any, Protocol

# Third-party
from liquipydia import (
    ApiResponse,
    LiquipediaClient,
    LiquipediaError,
    Match,
    StandingsTable,
    TeamTemplate,
    Tournament,
)

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
from ..models import EventInfo, LeagueConfig, Series

logger = logging.getLogger(__name__)


class LpdbStandingsTables(Protocol):
    """The `/standingstable` queries the source needs (liquipydia's `client.standings_tables`)."""

    def list(self, wiki: str, *, pagename: str, order: str | None = None) -> ApiResponse:
        """Return one page of the standings tables stored on pagename."""
        ...


class LpdbMatches(Protocol):
    """The `/match` queries the source needs (liquipydia's `client.matches`)."""

    def paginate(
        self, wiki: str, *, pagename: str, order: str | None = None, page_size: int = 50
    ) -> Iterator[dict[str, Any]]:
        """Iterate over every match stored on pagename, page by page."""
        ...


class LpdbTournaments(Protocol):
    """The `/tournament` queries the source needs (liquipydia's `client.tournaments`)."""

    def list(self, wiki: str, *, pagename: str, query: str | None = None) -> ApiResponse:
        """Return the tournament stored on pagename."""
        ...


class LpdbTeamTemplates(Protocol):
    """The `/teamtemplate` queries the source needs (liquipydia's `client.team_templates`)."""

    def get(self, wiki: str, template: str, *, date: str | None = None) -> ApiResponse:
        """Return the team template named template."""
        ...


class LpdbClient(Protocol):
    """The part of an LPDB client the source uses; `liquipydia.LiquipediaClient` satisfies it."""

    @property
    def standings_tables(self) -> LpdbStandingsTables:
        """Standings table queries."""
        ...

    @property
    def matches(self) -> LpdbMatches:
        """Match queries."""
        ...

    @property
    def tournaments(self) -> LpdbTournaments:
        """Tournament queries."""
        ...

    @property
    def team_templates(self) -> LpdbTeamTemplates:
        """Team template queries."""
        ...


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

    def __init__(self, client: LpdbClient | None = None) -> None:
        """Use `client`, or create one from `load_api_key()` on each fetch.

        Args:
            client: An open LPDB client (tests pass a fake one); None to open a liquipydia client per fetch.
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

    def fetch_event(self, config: LeagueConfig) -> EventInfo:
        """Return the event details from the league page's tournament record.

        Args:
            config: League format (`wiki` and `pagename` locate the tournament).

        Returns:
            The event details: organizers and competition logo, empty when the page holds no tournament record;
            team logos.

        Raises:
            DataSourceError: If the API fails.
            ConfigurationError: If no API key is available.
        """
        if self.client is not None:
            return _fetch_event(self.client, config)
        with LiquipediaClient(LPDB_APP_NAME, api_key=load_api_key()) as client:
            return _fetch_event(client, config)


def _fetch_event(client: LpdbClient, config: LeagueConfig) -> EventInfo:
    """Query the tournament record and the team templates, translating liquipydia errors into DataSourceError."""
    try:
        response = client.tournaments.list(
            config.wiki, pagename=config.pagename, query='pagename,organizers,iconurl,icondarkurl'
        )
        team_logo_urls = _team_logo_urls(client, config)
    except LiquipediaError as error:
        raise DataSourceError(f'Liquipedia request failed for {config.pagename}: {error}') from error
    if not response.result:
        logger.warning('No tournament record on %s: organizers unknown', config.pagename)
        return EventInfo(team_logo_urls=team_logo_urls)
    tournament = Tournament.model_validate(response.result[0])
    return EventInfo(
        organizers=parse_organizers(tournament.organizers),
        logo_url=tournament.icondarkurl or tournament.iconurl or '',
        team_logo_urls=team_logo_urls,
    )


def _team_logo_urls(client: LpdbClient, config: LeagueConfig) -> dict[str, str]:
    """Logo address of each team of the league page, from the team template of its latest match."""
    templates: dict[str, str] = {}
    records = client.matches.paginate(config.wiki, pagename=config.pagename, order='date ASC', page_size=LPDB_PAGE_SIZE)
    for record in records:
        for opponent in Match.model_validate(record).match2opponents or []:
            name, template = opponent.get('name'), opponent.get('template')
            if name and template:
                templates[config.team_aliases.get(str(name), str(name))] = str(template)
    urls = {}
    for team, template in templates.items():
        response = client.team_templates.get(config.wiki, template)
        url = team_logo_url(TeamTemplate.model_validate(response.result[0])) if response.result else ''
        if url:
            urls[team] = url
    return urls


def team_logo_url(template: TeamTemplate) -> str:
    """Return the logo address of a team template, dark theme version first.

    Args:
        template: The LPDB team template.

    Returns:
        The address; empty when the template has none. A team without branding of its own gets Liquipedia's
        default logo of the game (e.g. `Rocket League default darkmode.png`), kept as is.
    """
    return template.imagedarkurl or template.imageurl or ''


def parse_organizers(text: str | None) -> tuple[str, ...]:
    """Parse the `organizers` field of an LPDB tournament, a JSON object such as `{"organizer1": "NEO"}`.

    Args:
        text: Raw field value (None or empty when unset).

    Returns:
        The organizer names in key order (`organizer1`, `organizer2`, ...); empty when unset or unreadable.
    """
    try:
        data = json.loads(text or '{}')
    except json.JSONDecodeError:
        logger.warning('Unreadable tournament organizers: %r', text)
        return ()
    if not isinstance(data, dict):
        return ()
    numbered = sorted(data.items(), key=lambda item: int(re.sub(r'\D', '', str(item[0])) or 0))
    return tuple(str(name) for _, name in numbered if name)


def _fetch(client: LpdbClient, config: LeagueConfig) -> list[Series]:
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
        whose games are not all scored (goal tiebreakers then treat it as unknown). `played_on` is its day in the
        event's timezone (`local_day`).

    Raises:
        DataSourceError: If the match does not have two named opponents.
    """
    first, second = _opponents(match)
    finished, played_on = bool(match.finished), local_day(match)
    statuses = (first.get('status') or '', second.get('status') or '')
    if any(status in STATUS_WALKOVERS for status in statuses):
        score_1, score_2 = _walkover_scores(statuses, config.walkover_games)
        return Series(round_number, first['name'], second['name'], score_1, score_2, finished, (), played_on)

    score_1, score_2 = _score(first), _score(second)
    goals = _goals(match, expected_games=score_1 + score_2 if finished else None)
    return Series(round_number, first['name'], second['name'], score_1, score_2, finished, goals, played_on)


def local_day(match: Match) -> date | None:
    """Return the day of a match in the event's timezone.

    LPDB stores match dates in UTC, with the offset of the timezone the page gives them in (e.g. `+01:00` for CET)
    in `extradata.timezoneoffset`: a late UTC evening can already be the next day where the event takes place.

    Args:
        match: The LPDB match.

    Returns:
        The local day; the UTC day when no offset is recorded or the date has no time (`dateexact` unset); None
        without a date.
    """
    if match.date is None:
        return None
    offset = parse_utc_offset((match.extradata or {}).get('timezoneoffset')) if match.dateexact else None
    return (match.date + (offset or timedelta())).date()


def parse_utc_offset(text: object) -> timedelta | None:
    """Parse a UTC offset such as `+01:00`, `-05:30` or `+2`.

    Args:
        text: Raw offset (None or empty when unset).

    Returns:
        The offset; None when unset or unreadable.
    """
    found = re.fullmatch(r'([+-])(\d{1,2})(?::?(\d{2}))?', str(text or '').strip())
    if found is None:
        if text:
            logger.warning('Unreadable timezone offset: %r', text)
        return None
    sign, hours, minutes = found.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes or 0))
    return -offset if sign == '-' else offset


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
