"""CSV results: offline snapshots, manual input and test fixtures.

Columns: `round, team_1, team_2, score_1, score_2, finished, goals, date`. `finished` is `1` or `0`; `goals` lists
the goals of each game as `team_1-team_2` separated by `;` (e.g. `7-2;2-0;4-1`), `-` when known to be none (a walkover
or an upcoming series), empty when unknown; `date` is the day of the series in the event's timezone (`YYYY-MM-DD`),
empty or absent when unknown.
"""

# Standard library
import csv
from collections.abc import Iterable
from datetime import date
from pathlib import Path

# Local
from ...exceptions import DataSourceError
from ..config import league_dir
from ..constants import NO_GOALS, RESULTS_COLUMNS, RESULTS_CSV, RESULTS_OPTIONAL_COLUMNS
from ..models import EventInfo, LeagueConfig, Series
from .event import event_path, read_event


def results_path(config: LeagueConfig) -> Path:
    """Return the league's results snapshot, `_data/<league>/results.csv`.

    Args:
        config: League format.

    Returns:
        The snapshot path (read from `paths` at call time).
    """
    return league_dir(config.slug) / RESULTS_CSV


class CsvSource:
    """Read league series from a CSV file: the league's results snapshot, or a given file."""

    def __init__(self, path: Path | None = None) -> None:
        """Read results from `path`, or from the league's snapshot (`results_path`) when None.

        Args:
            path: CSV file (fixtures, manual input); None for the league's snapshot.
        """
        self.path = path

    def fetch(self, config: LeagueConfig) -> list[Series]:
        """Return the series stored in the CSV file.

        Args:
            config: League format, locating its snapshot when no file was given.

        Returns:
            The series, in file order.

        Raises:
            DataSourceError: If the file is missing or a row is invalid.
        """
        path = self.path or results_path(config)
        try:
            with path.open(encoding='utf-8', newline='') as file:
                reader = csv.DictReader(file)
                missing = set(RESULTS_COLUMNS) - RESULTS_OPTIONAL_COLUMNS - set(reader.fieldnames or ())
                if missing:
                    raise DataSourceError(f'{path}: missing columns {", ".join(sorted(missing))}')
                return [_parse_row(row, line) for line, row in enumerate(reader, start=2)]
        except FileNotFoundError as error:
            raise DataSourceError(f'Results file not found: {path}') from error

    @staticmethod
    def fetch_event(config: LeagueConfig) -> EventInfo:
        """Return the event details saved by the last Liquipedia run (`event_path`), whatever file `path` is.

        Args:
            config: League format, locating its snapshot.

        Returns:
            The event details, empty when no snapshot exists.

        Raises:
            DataSourceError: If the snapshot is invalid.
        """
        return read_event(event_path(config))


def write_results(series: Iterable[Series], path: Path) -> None:
    """Write series to a CSV file readable by `CsvSource`.

    Args:
        series: Series to write.
        path: Target file (parent folders are created).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        writer.writerow(RESULTS_COLUMNS)
        for item in series:
            goals = _format_goals(item.goals)
            day = item.played_on.isoformat() if item.played_on else ''
            writer.writerow(
                (item.round, item.team_1, item.team_2, item.score_1, item.score_2, int(item.finished), goals, day)
            )


def _parse_row(row: dict[str, str], line: int) -> Series:
    """Build a series from a CSV row, raising DataSourceError with the line number on invalid data."""
    try:
        return Series(
            round=int(row['round']),
            team_1=row['team_1'].strip(),
            team_2=row['team_2'].strip(),
            score_1=int(row['score_1'] or 0),
            score_2=int(row['score_2'] or 0),
            finished=row['finished'].strip() == '1',
            goals=_parse_goals(row['goals'].strip()),
            played_on=_parse_day((row.get('date') or '').strip()),
        )
    except ValueError as error:
        raise DataSourceError(f'Invalid row at line {line}: {error}') from error


def _format_goals(goals: tuple[tuple[int, int], ...] | None) -> str:
    """Format per-game goals: empty when unknown, `-` when known to be none."""
    if goals is None:
        return ''
    return ';'.join(f'{one}-{two}' for one, two in goals) or NO_GOALS


def _parse_day(text: str) -> date | None:
    """Parse a `YYYY-MM-DD` day, None when empty."""
    return date.fromisoformat(text) if text else None


def _parse_goals(text: str) -> tuple[tuple[int, int], ...] | None:
    """Parse per-game goals written by `_format_goals`."""
    if not text:
        return None
    if text == NO_GOALS:
        return ()
    return tuple((int(one), int(two)) for one, two in (game.split('-') for game in text.split(';')))
