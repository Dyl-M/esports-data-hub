"""Event details snapshot: `_data/<league>/event.json`, written by each Liquipedia run, read back offline.

Layout: `{"organizers": ["NEO"]}`. A missing file means the details are unknown, not an error.
"""

# Standard library
import json
from pathlib import Path

# Local
from ...exceptions import DataSourceError
from ..config import league_dir
from ..constants import EVENT_JSON
from ..models import EventInfo, LeagueConfig


def event_path(config: LeagueConfig) -> Path:
    """Return the league's event details snapshot, `_data/<league>/event.json`.

    Args:
        config: League format.

    Returns:
        The snapshot path (read from `paths` at call time).
    """
    return league_dir(config.slug) / EVENT_JSON


def write_event(event: EventInfo, path: Path) -> None:
    """Write event details readable by `read_event`.

    Args:
        event: Event details.
        path: Target file (parent folders are created).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'organizers': list(event.organizers)}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


def read_event(path: Path) -> EventInfo:
    """Read event details written by `write_event`.

    Args:
        path: Snapshot file.

    Returns:
        The event details; empty ones when the file does not exist.

    Raises:
        DataSourceError: If the file is not valid JSON or `organizers` is not a list of names.
    """
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return EventInfo()
    except json.JSONDecodeError as error:
        raise DataSourceError(f'Invalid JSON in {path}: {error}') from error
    organizers = data.get('organizers', []) if isinstance(data, dict) else None
    if not isinstance(organizers, list) or not all(isinstance(name, str) for name in organizers):
        raise DataSourceError(f'{path}: "organizers" must be a list of names')
    return EventInfo(organizers=tuple(organizers))
