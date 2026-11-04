"""League format loading from `_data/<league>/league.json`."""

# Standard library
import json
from pathlib import Path
from typing import Any

# Local
from .. import paths
from ..exceptions import ConfigurationError
from .constants import LEAGUE_JSON
from .models import LeagueConfig, QualificationSlot, Tiebreaker

REQUIRED_KEYS = ('name', 'wiki', 'pagename', 'best_of', 'tiebreakers')


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


def parse_league(slug: str, data: Any, *, file_path: str | None = None) -> LeagueConfig:  # noqa: ANN401 - raw JSON
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
            name=str(data['name']),
            wiki=str(data['wiki']),
            pagename=str(data['pagename']),
            best_of=_integer(data['best_of'], 'best_of'),
            tiebreakers=tuple(Tiebreaker.parse(str(value)) for value in data['tiebreakers']),
            qualification=tuple(
                QualificationSlot(label=label, first=_integer(first, label), last=_integer(last, label))
                for label, (first, last) in data.get('qualification', {}).items()
            ),
            walkover_games=_integer(data.get('walkover_games', 0), 'walkover_games'),
            team_aliases={str(old): str(new) for old, new in data.get('team_aliases', {}).items()},
            manual_tiebreakers={
                str(team): _integer(points, team) for team, points in data.get('manual_tiebreakers', {}).items()
            },
        )
    except (TypeError, ValueError) as error:
        raise ConfigurationError(f'Invalid league file: {error}', file_path=file_path) from error


def _integer(value: object, name: str) -> int:
    """Return `value` if it is an integer (JSON `true` / `false` rejected), else raise ValueError."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be an integer, got {value!r}')
    return value
