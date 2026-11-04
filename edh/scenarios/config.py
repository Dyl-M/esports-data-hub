"""League format loading from `_data/<league>/league.json`."""

# Standard library
import json
from pathlib import Path

# Local
from .. import paths
from ..exceptions import ConfigurationError
from .constants import LEAGUE_JSON
from .models import LeagueConfig, QualificationSlot, Tiebreaker

REQUIRED_KEYS = ('name', 'wiki', 'pagename', 'best_of', 'tiebreakers')

# JSON type of each decoded Python type, for error messages
JSON_TYPES: dict[type, str] = {
    bool: 'a boolean (true / false)',
    int: 'an integer number',
    float: 'a decimal number',
    str: 'a string',
    list: 'a list (JSON array)',
    dict: 'an object (JSON key / value mapping)',
    type(None): 'null',
}


def league_dir(slug: str) -> Path:
    """Return the data folder of a league (read from `paths` at call time, so tests can redirect it).

    Args:
        slug: League folder name in `_data/`.

    Returns:
        The league folder.
    """
    return paths.DATA_DIR / slug


def load_league(slug: str) -> LeagueConfig:
    """Load and validate a league format.

    Args:
        slug: League folder name in `_data/`.

    Returns:
        The league format.

    Raises:
        ConfigurationError: If the file is missing, malformed or invalid.
    """
    file_path = league_dir(slug) / LEAGUE_JSON
    try:
        data = json.loads(file_path.read_text(encoding='utf-8'))
    except FileNotFoundError as error:
        raise ConfigurationError(f'League file not found: {file_path}', file_path=str(file_path)) from error
    except json.JSONDecodeError as error:
        raise ConfigurationError(f'Invalid JSON in {file_path}: {error}', file_path=str(file_path)) from error
    return parse_league(slug, data, file_path=str(file_path))


def parse_league(slug: str, data: object, *, file_path: str | None = None) -> LeagueConfig:
    """Build a league format from its JSON content.

    Args:
        slug: League folder name in `_data/`.
        data: Decoded `league.json` content.
        file_path: File the data comes from, for error messages.

    Returns:
        The league format.

    Raises:
        ConfigurationError: If keys are missing or values are invalid.
    """
    if not isinstance(data, dict):
        raise ConfigurationError('League file must hold a JSON object', file_path=file_path)
    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise ConfigurationError(f'Missing keys in league file: {", ".join(missing)}', file_path=file_path)
    try:
        return LeagueConfig(
            slug=slug,
            name=_string(data['name'], 'name'),
            wiki=_string(data['wiki'], 'wiki'),
            pagename=_string(data['pagename'], 'pagename'),
            best_of=_integer(data['best_of'], 'best_of'),
            tiebreakers=tuple(
                Tiebreaker.parse(_string(value, 'tiebreakers')) for value in _list(data['tiebreakers'], 'tiebreakers')
            ),
            qualification=tuple(
                _qualification_slot(label, value)
                for label, value in _mapping(data.get('qualification', {}), 'qualification').items()
            ),
            walkover_games=_integer(data.get('walkover_games', 0), 'walkover_games'),
            team_aliases={
                old: _string(new, old) for old, new in _mapping(data.get('team_aliases', {}), 'team_aliases').items()
            },
            manual_tiebreakers={
                team: _integer(points, team)
                for team, points in _mapping(data.get('manual_tiebreakers', {}), 'manual_tiebreakers').items()
            },
        )
    except ValueError as error:
        raise ConfigurationError(f'Invalid league file: {error}', file_path=file_path) from error


def _qualification_slot(label: str, value: object) -> QualificationSlot:
    """Build a qualification slot from its `[first, last]` rank range, else raise ValueError."""
    bounds = _list(value, label)
    if len(bounds) != 2:
        raise ValueError(f'{label} must be a [first, last] rank range, got {len(bounds)} values')
    return QualificationSlot(label=label, first=_integer(bounds[0], label), last=_integer(bounds[1], label))


def _integer(value: object, name: str) -> int:
    """Return `value` if it is an integer (JSON `true` / `false` rejected), else raise ValueError."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be an integer, got {_json_type(value)}')
    return value


def _string(value: object, name: str) -> str:
    """Return `value` if it is a string, else raise ValueError."""
    if not isinstance(value, str):
        raise ValueError(f'{name} must be a string, got {_json_type(value)}')
    return value


def _list(value: object, name: str) -> list[object]:
    """Return `value` if it is a JSON array, else raise ValueError."""
    if not isinstance(value, list):
        raise ValueError(f'{name} must be a list, got {_json_type(value)}')
    return value


def _mapping(value: object, name: str) -> dict[str, object]:
    """Return `value` if it is a JSON object (string keys), else raise ValueError."""
    if not isinstance(value, dict):
        raise ValueError(f'{name} must be an object, got {_json_type(value)}')
    return value


def _json_type(value: object) -> str:
    """Name the JSON type of a decoded value (e.g. `a boolean`), for error messages."""
    return JSON_TYPES.get(type(value), type(value).__name__)
