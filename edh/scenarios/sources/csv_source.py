"""CSV results: offline snapshots, manual input and test fixtures.

Columns: `round, team_1, team_2, score_1, score_2, finished, goals`. `finished` is `1` or `0`; `goals` lists the
goals of each game as `team_1-team_2` separated by `;` (e.g. `7-2;2-0;4-1`), `-` when known to be none (a walkover
or an upcoming series), empty when unknown.
"""

# Standard library
import csv
from collections.abc import Iterable
from pathlib import Path

# Local
from ...exceptions import DataSourceError
from ..constants import NO_GOALS, RESULTS_COLUMNS
from ..models import LeagueConfig, Series


class CsvSource:
    """Read league series from a CSV file."""

    def __init__(self, path: Path) -> None:
        """Read results from `path`.

        Args:
            path: CSV file.
        """
        self.path = path

    def fetch(self, config: LeagueConfig) -> list[Series]:
        """Return the series stored in the CSV file.

        Args:
            config: League format (unused: the file only holds this league).

        Returns:
            The series, in file order.

        Raises:
            DataSourceError: If the file is missing or a row is invalid.
        """
        try:
            with self.path.open(encoding='utf-8', newline='') as file:
                reader = csv.DictReader(file)
                missing = set(RESULTS_COLUMNS) - set(reader.fieldnames or ())
                if missing:
                    raise DataSourceError(f'{self.path}: missing columns {", ".join(sorted(missing))}')
                return [_parse_row(row, line) for line, row in enumerate(reader, start=2)]
        except FileNotFoundError as error:
            raise DataSourceError(f'Results file not found: {self.path}') from error


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
            writer.writerow(
                (item.round, item.team_1, item.team_2, item.score_1, item.score_2, int(item.finished), goals)
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
        )
    except ValueError as error:
        raise DataSourceError(f'Invalid row at line {line}: {error}') from error


def _format_goals(goals: tuple[tuple[int, int], ...] | None) -> str:
    """Format per-game goals: empty when unknown, `-` when known to be none."""
    if goals is None:
        return ''
    return ';'.join(f'{one}-{two}' for one, two in goals) or NO_GOALS


def _parse_goals(text: str) -> tuple[tuple[int, int], ...] | None:
    """Parse per-game goals written by `_format_goals`."""
    if not text:
        return None
    if text == NO_GOALS:
        return ()
    return tuple((int(one), int(two)) for one, two in (game.split('-') for game in text.split(';')))
