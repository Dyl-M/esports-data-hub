"""Frozen forecasts: every pair of an event's teams, forecast once before it starts and saved, to be scored after.

The teams and their rosters are read from the event's placements on Liquipedia (every slot must be filled), then each
pair is forecast for the event's first day with every model and identity, as tuned by `predict --tune`, from the cached
history. A frozen file is never overwritten. Each event gets its own script in `_scripts/` (event page, day, file).
"""

# Standard library
import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from itertools import product
from pathlib import Path
from typing import Any

# Third-party
from liquipydia import LiquipediaError, Placement

# Local
from ..exceptions import ConfigurationError, DataSourceError
from ..lpdb import LPDB_MAX_PAGE_SIZE, LpdbPlacementClient, page_condition
from .constants import IDENTITIES, IDENTITY_NAME, PLAYER_KEY, TBD_TEAM, WIKI
from .evaluate import write_json
from .forecast import Forecast
from .history import read_history
from .lineups import identify_lineups
from .predict import Pair, Picks, Task, forecast_all, latest_picks, select_picks
from .tuning import MODELS

logger = logging.getLogger(__name__)

PLACEMENT_FIELDS = 'opponentname, opponentplayers, qualifier'

# === Freezing ===


def freeze(
    client: LpdbPlacementClient, page: str, day: dt.date, path: Path, now: dt.datetime | None = None
) -> dict[str, Any]:
    """Forecast every pair of an event's teams with each model and identity, and save the frozen file.

    Args:
        client: An open LPDB client.
        page: The event's page.
        day: The event's first day: every forecast is for its start (UTC).
        path: The frozen file to write.
        now: The time of the freeze, timezone-aware (None: the current time).

    Returns:
        The frozen file's content: the event, the day forecast, when it was frozen, the day of the latest match before
        the event, then the forecasts (`forecasts_content`).

    Raises:
        ConfigurationError: If the file is already frozen, or the event has started.
        DataSourceError: If the event's teams can't be read or aren't all known, the history has no match before the
            event, or a model or identity was never tuned.
    """
    now = now or dt.datetime.now(dt.UTC)
    when = dt.datetime.combine(day, dt.time(), dt.UTC)
    if path.exists():
        raise ConfigurationError(f'Already frozen: {path} (delete it to freeze again)', file_path=str(path))
    if now >= when:
        raise ConfigurationError(f'Too late to freeze: the event started on {day}', file_path=str(path))
    teams = event_teams(client, page)
    matches = [match for match in read_history() if match.played_at < when]
    if not matches:
        raise DataSourceError(f'No match cached before {day}: run `python -m edh.predictions.run`')
    picks = select_picks(latest_picks(), list(product(MODELS, IDENTITIES)))
    pairs = forecast_all(matches, identify_lineups(matches), picks, when, teams)
    content: dict[str, Any] = {
        'event': page,
        'forecast_day': day.isoformat(),
        'frozen_at': now.isoformat(timespec='seconds'),
        'history_through': max(match.played_at for match in matches).date().isoformat(),
        **forecasts_content(teams, picks, pairs),
    }
    new_teams = [team['team'] for team in content['teams'] if team['never_rated'][IDENTITY_NAME]]
    if new_teams:
        logger.warning(
            'Never seen under their name (cold starts for the name and lineup identities): %s', ', '.join(new_teams)
        )
    write_json(content, path)
    return content


# === Teams ===


def event_teams(client: LpdbPlacementClient, page: str) -> dict[str, tuple[str, ...]]:
    """Return the teams of an event and their rosters, from its placements on Liquipedia.

    An event's teams are its placements with a qualifier (how the team got in); the other placements are its final
    places, filled once played.

    Args:
        client: An open LPDB client.
        page: The event's page.

    Returns:
        Each team's roster (player pages, sorted), by team name, sorted.

    Raises:
        DataSourceError: If LPDB fails, the page has no team, or a slot is not filled yet (team or roster unknown).
    """
    try:
        records = list(
            client.placements.paginate(
                WIKI, conditions=page_condition(page), query=PLACEMENT_FIELDS, page_size=LPDB_MAX_PAGE_SIZE
            )
        )
    except LiquipediaError as error:
        raise DataSourceError(f'LPDB placements of {page} unavailable ({error})') from error
    entrants = [placement for placement in map(Placement.model_validate, records) if placement.qualifier]
    if not entrants:
        raise DataSourceError(f'No team on {page} yet')
    unknown = [_unknown(placement) for placement in entrants if not (_named(placement) and _roster(placement))]
    if unknown:
        raise DataSourceError(f'Not every team of {page} is known yet: {", ".join(sorted(unknown))}')
    return dict(sorted((str(placement.opponentname), _roster(placement)) for placement in entrants))


def _roster(placement: Placement) -> tuple[str, ...]:
    """The player pages of a placement, sorted (empty when not filled)."""
    players = placement.opponentplayers or {}
    return tuple(sorted(str(page) for key, page in players.items() if PLAYER_KEY.fullmatch(key) and page))


def _named(placement: Placement) -> bool:
    """Whether a placement names its team (not empty, not `TBD`)."""
    return placement.opponentname not in {None, '', TBD_TEAM}


def _unknown(placement: Placement) -> str:
    """How an unfilled slot is named: its qualifier when the team is not known, else the team without roster."""
    if _named(placement):
        return f'{placement.opponentname} (no roster)'
    return f'{placement.qualifier} winner'


# === Frozen file ===


def forecasts_content(teams: Mapping[str, Sequence[str]], picks: Picks, pairs: Sequence[Pair]) -> dict[str, Any]:
    """Return the forecasts part of a frozen file.

    Args:
        teams: Each team's roster, by name.
        picks: Hyperparameters of each model and identity forecast.
        pairs: The forecasts of every pair.

    Returns:
        The content, ready for JSON: the picks, each team's roster and members never rated per identity, and each
        pair's per-game probability that its first team wins, by model and identity.
    """
    never_rated: dict[str, dict[str, list[str]]] = {team: {} for team in teams}
    for first, second, forecasts in pairs:
        for (_, identity), forecast in forecasts.items():  # the same for every model of an identity
            first_unseen, second_unseen = forecast.unseen
            never_rated[first][identity] = list(first_unseen)
            never_rated[second][identity] = list(second_unseen)
    return {
        'picks': [
            {'model': model, 'identity': identity, 'params': dict(params)}
            for (model, identity), params in picks.items()
        ],
        'teams': [
            {'team': team, 'roster': list(roster), 'never_rated': never_rated[team]} for team, roster in teams.items()
        ],
        'pairs': [
            {'first': first, 'second': second, 'game_probability': _by_model(forecasts)}
            for first, second, forecasts in pairs
        ],
    }


def _by_model(forecasts: Mapping[Task, Forecast]) -> dict[str, dict[str, float]]:
    """Per-game probabilities of the first team, by model then identity."""
    probabilities: dict[str, dict[str, float]] = {}
    for (model, identity), forecast in forecasts.items():
        probabilities.setdefault(model, {})[identity] = forecast.game_probability
    return probabilities
