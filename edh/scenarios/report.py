"""Scenario report rendering (console table) and export (CSV / JSON)."""

# Standard library
import csv
import json
from dataclasses import asdict
from pathlib import Path

# Local
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
            _percent(outlook.unresolved).rjust(6),
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


def _rank_cell(outlook: TeamOutlook, rank: int) -> str:
    """Format the share of one rank, bracketed when it is the team's projected rank."""
    text = _percent(outlook.rank_shares[rank - 1])
    return f'[{text}]' if rank == outlook.projected_rank else text


def _slot_width(label: str) -> int:
    """Column width of a qualification slot, wide enough for `99.9% (+99.9%)`."""
    return max(len(label), 14)


def _percent(share: float) -> str:
    """Format a share as a percentage, keeping 0% and 100% for exact values only."""
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
    text = _percent(qualified)
    return f'{text} (+{_percent(undecided)})' if undecided else text
