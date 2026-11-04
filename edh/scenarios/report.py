"""Scenario report rendering (console table), export (CSV / JSON) and reading back (JSON)."""

# Standard library
import csv
import json
from dataclasses import asdict
from pathlib import Path

# Local
from ..exceptions import DataSourceError
from .constants import OUTLOOK_CSV, OUTLOOK_JSON
from .models import ScenarioReport, TeamOutlook

RANK_WIDTH = 7  # fits `[99.9%]`


def format_table(report: ScenarioReport) -> str:
    """Render the report as a plain-text table.

    Args:
        report: Evaluated scenarios.

    Returns:
        The table, with a header line and a trailing newline.
    """
    kind = 'exact' if report.exact else 'estimated, Monte Carlo'
    lines = [
        f'{report.league}: {report.remaining} series left, {report.scenarios:,} scenarios ({kind})',
        '',
    ]
    labels, ranks = report.slot_labels, report.rank_count
    width = max([len('Team'), *(len(outlook.team) for outlook in report.teams)])
    header = [
        'Team'.ljust(width),
        'Avg',
        'Proj',
        *(f'#{rank}'.rjust(RANK_WIDTH) for rank in range(1, ranks + 1)),
        *(label.rjust(_slot_width(label)) for label in labels),
        'Tied*'.rjust(6),
    ]
    lines.append('  '.join(header))
    for outlook in report.teams:
        row = [
            outlook.team.ljust(width),
            f'{outlook.expected_rank:.2f}',
            f'#{outlook.projected_rank}'.rjust(len('Proj')),
            *(_rank_cell(outlook, rank).rjust(RANK_WIDTH) for rank in range(1, ranks + 1)),
            *(_qualification(outlook, label, exact=report.exact).rjust(_slot_width(label)) for label in labels),
            format_share(outlook.unresolved).rjust(6),
        ]
        lines.append('  '.join(row))
    lines.extend(
        (
            '',
            'Proj [x%]: projected rank, one team per rank, from the most likely team / rank pairing down.',
            '* Tied: share of scenarios ending in a tie that only goal difference or a manual decision settles.',
        )
    )
    if labels:
        lines.append('  Qualification shares exclude those ties; "(+x%)" is the share still decided by them.')
    return '\n'.join(lines) + '\n'


def write_outlook(report: ScenarioReport, folder: Path) -> tuple[Path, Path]:
    """Export the per-team outlook as CSV and JSON.

    Args:
        report: Evaluated scenarios.
        folder: Target folder (created if needed).

    Returns:
        The CSV and JSON paths.
    """
    folder.mkdir(parents=True, exist_ok=True)
    csv_path, json_path = folder / OUTLOOK_CSV, folder / OUTLOOK_JSON
    data = asdict(report)
    for team, outlook in zip(data['teams'], report.teams, strict=True):
        team['expected_rank'] = outlook.expected_rank
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    labels, ranks = report.slot_labels, report.rank_count
    with csv_path.open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        writer.writerow(
            [
                'team',
                'expected_rank',
                'projected_rank',
                *(f'rank_{rank}' for rank in range(1, ranks + 1)),
                *(f'{label}_qualified' for label in labels),
                *(f'{label}_undecided' for label in labels),
                'unresolved',
            ]
        )
        for outlook in report.teams:
            writer.writerow(
                [
                    outlook.team,
                    round(outlook.expected_rank, 4),
                    outlook.projected_rank,
                    *(round(share, 6) for share in outlook.rank_shares),
                    *(round(outlook.qualified[label], 6) for label in labels),
                    *(round(outlook.undecided[label], 6) for label in labels),
                    round(outlook.unresolved, 6),
                ]
            )
    return csv_path, json_path


def read_outlook(path: Path) -> ScenarioReport:
    """Read back a report exported by `write_outlook` (JSON).

    Args:
        path: `outlook.json` file.

    Returns:
        The report.

    Raises:
        DataSourceError: If the file is missing or does not hold a valid report.
    """
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        teams = tuple(
            TeamOutlook(
                team=str(team['team']),
                rank_shares=tuple(float(share) for share in team['rank_shares']),
                qualified={str(label): float(share) for label, share in team['qualified'].items()},
                undecided={str(label): float(share) for label, share in team['undecided'].items()},
                unresolved=float(team['unresolved']),
                projected_rank=int(team['projected_rank']) if team.get('projected_rank') is not None else None,
            )
            for team in data['teams']
        )
        return ScenarioReport(
            league=str(data['league']),
            method=str(data['method']),
            scenarios=int(data['scenarios']),
            remaining=int(data['remaining']),
            teams=teams,
        )
    except FileNotFoundError as error:
        raise DataSourceError(f'Outlook file not found: {path} (evaluate the league first)') from error
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as error:
        raise DataSourceError(f'Invalid outlook file {path}: {error!r}') from error


def _rank_cell(outlook: TeamOutlook, rank: int) -> str:
    """Format the share of one rank, bracketed when it is the team's projected rank."""
    text = format_share(outlook.rank_shares[rank - 1])
    return f'[{text}]' if rank == outlook.projected_rank else text


def _slot_width(label: str) -> int:
    """Column width of a qualification slot, wide enough for `99.9% (+99.9%)`."""
    return max(len(label), 14)


def format_share(share: float) -> str:
    """Format a share as a percentage, keeping 0% and 100% for exact values only.

    Args:
        share: Share between 0 and 1.

    Returns:
        `-` for 0, `100%` for 1, else a percentage with one decimal clamped to 0.1%-99.9%.
    """
    if share == 0:
        return '-'
    if share == 1:
        return '100%'
    return f'{min(max(share * 100, 0.1), 99.9):.1f}%'


def _qualification(outlook: TeamOutlook, label: str, *, exact: bool) -> str:
    """Format the qualification share of a slot, with the share still decided by goal-difference ties."""
    qualified, undecided = outlook.qualified[label], outlook.undecided[label]
    if exact and qualified == 1:
        return 'IN'
    if exact and qualified == 0 and undecided == 0:
        return 'OUT'
    text = format_share(qualified)
    return f'{text} (+{format_share(undecided)})' if undecided else text
