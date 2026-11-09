"""League format loading from `_data/<league>/league.json`."""

# Standard library
import json
from dataclasses import fields
from pathlib import Path

# Local
from .. import paths
from ..exceptions import ConfigurationError
from .constants import LEAGUE_JSON
from .models import LeagueConfig, QualificationSlot, Theme, Tiebreaker

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


def league_path(config: LeagueConfig, *parts: str) -> Path:
    """Return a path inside the league's data folder.

    Args:
        config: League format.
        *parts: Path segments below `_data/<league>/`, file or folder names from `constants` (e.g. `RESULTS_CSV`,
            or `OUTLOOK_DIR` followed by the day and the method).

    Returns:
        The path (read from `paths` at call time).
    """
    return league_dir(config.slug).joinpath(*parts)


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
            name=_typed(data['name'], str, 'name'),
            wiki=_typed(data['wiki'], str, 'wiki'),
            pagename=_typed(data['pagename'], str, 'pagename'),
            best_of=_integer(data['best_of'], 'best_of'),
            tiebreakers=tuple(
                Tiebreaker.parse(_typed(value, str, 'tiebreakers'))
                for value in _typed(data['tiebreakers'], list, 'tiebreakers')
            ),
            qualification=tuple(
                _qualification_slot(label, value)
                for label, value in _typed(data.get('qualification', {}), dict, 'qualification').items()
            ),
            walkover_games=_integer(data.get('walkover_games', 0), 'walkover_games'),
            team_aliases={
                old: _typed(new, str, old)
                for old, new in _typed(data.get('team_aliases', {}), dict, 'team_aliases').items()
            },
            manual_tiebreakers={
                team: _integer(points, team)
                for team, points in _typed(data.get('manual_tiebreakers', {}), dict, 'manual_tiebreakers').items()
            },
            theme=_theme(_typed(data.get('theme', {}), dict, 'theme')),
        )
    except ValueError as error:
        raise ConfigurationError(f'Invalid league file: {error}', file_path=file_path) from error


def _theme(colors: dict[str, object]) -> Theme:
    """Build a card theme from the colors given (the others keep their neutral value), else raise ValueError."""
    unknown = set(colors) - {item.name for item in fields(Theme)}
    if unknown:
        raise ValueError(f'unknown theme colors: {", ".join(sorted(unknown))}')
    return Theme(**{name: _typed(value, str, f'theme.{name}') for name, value in colors.items()})


def _qualification_slot(label: str, value: object) -> QualificationSlot:
    """Build a qualification slot from its `[first, last]` rank range, else raise ValueError."""
    bounds = _typed(value, list, label)
    if len(bounds) != 2:
        raise ValueError(f'{label} must be a [first, last] rank range, got {len(bounds)} values')
    return QualificationSlot(label=label, first=_integer(bounds[0], label), last=_integer(bounds[1], label))


def _integer(value: object, name: str) -> int:
    """Return `value` if it is an integer (JSON `true` / `false` rejected), else raise ValueError."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be an integer, got {_json_type(value)}')
    return value


def _typed[T](value: object, kind: type[T], name: str) -> T:
    """Return `value` if it is of the JSON type `kind` (`str`, `list` or `dict`), else raise ValueError."""
    if not isinstance(value, kind):
        raise ValueError(f'{name} must be {JSON_TYPES[kind]}, got {_json_type(value)}')
    return value


def _json_type(value: object) -> str:
    """Name the JSON type of a decoded value (e.g. `a boolean`), for error messages."""
    return JSON_TYPES.get(type(value), type(value).__name__)
