"""Evaluate the rating baselines on the cached match history.

Each model is tuned for each identity on the tuning season, then the evaluation season is scored once. The comparison
is logged and saved to `_data/predictions/<last match day>/` (`evaluation.json`, `calibration.csv`).
"""

# Standard library
import argparse
import csv
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict, astuple, fields
from pathlib import Path
from typing import Any

# Local
from .. import paths
from ..exceptions import EsportsDataHubError
from .constants import (
    CALIBRATION_CSV,
    EVALUATION_JSON,
    EVALUATION_SEASON,
    IDENTITIES,
    PREDICTIONS_FOLDER,
    REPORT_DIGITS,
    TUNING_SEASON,
)
from .evaluation import MODELS, CalibrationBin, Result, breakdown, calibration, evaluate_all, first_team_share, score
from .history import read_history
from .lineups import identify_lineups

logger = logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: Arguments, without the program name; `sys.argv[1:]` when None.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(prog='python -m edh.predictions.evaluate', description=__doc__.splitlines()[0])
    parser.add_argument('--models', nargs='+', choices=list(MODELS), default=list(MODELS), help='models to evaluate')
    parser.add_argument('--identities', nargs='+', choices=IDENTITIES, default=list(IDENTITIES), help='identities')
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Evaluate the models, log the comparison and save it.

    Args:
        argv: Arguments, without the program name; `sys.argv[1:]` when None.

    Returns:
        The exit code: 0 on success, 1 on error.
    """
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    try:
        matches = read_history()
        results = evaluate_all(matches, identify_lineups(matches), args.models, args.identities)
    except EsportsDataHubError as error:
        logger.error('%s', error)
        return 1
    for line in summary(results):
        logger.info('%s', line)
    folder = paths.DATA_DIR / PREDICTIONS_FOLDER / max(match.played_at for match in matches).date().isoformat()
    write_report(results, folder)
    logger.info('Saved to %s', folder)
    return 0


def summary(results: Sequence[Result]) -> list[str]:
    """Lines of the comparison table: series log loss, Brier score, accuracy and game log loss of the evaluation season.

    Args:
        results: Tuned models.

    Returns:
        A header line, one line per model and identity (best series log loss first), then the share of the series won
        by the team listed first.
    """
    lines = [
        f'RLCS {EVALUATION_SEASON} (tuned on RLCS {TUNING_SEASON}): '
        'model / identity / log loss / Brier / accuracy / game log loss / parameters'
    ]
    scored = sorted(((score(result.predictions), result) for result in results), key=lambda pair: pair[0].log_loss)
    for scores, result in scored:
        params = ', '.join(f'{name}={value:g}' for name, value in result.params.items()) or '-'
        lines.append(
            f'{result.model:<14} {result.identity:<8} {scores.log_loss:.4f} {scores.brier:.4f} '
            f'{scores.accuracy:.3f} {scores.game_log_loss:.4f}  {params}'
        )
    if results:
        lines.append(f'The first team listed won {first_team_share(results[0].predictions):.1%} of these series')
    return lines


def write_report(results: Sequence[Result], folder: Path) -> None:
    """Save the evaluation: scores per group in `evaluation.json`, calibration bins in `calibration.csv`.

    Args:
        results: Tuned models.
        folder: Output folder, created when missing.
    """
    folder.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        'tuning_season': TUNING_SEASON,
        'evaluation_season': EVALUATION_SEASON,
        'first_team_share': first_team_share(results[0].predictions) if results else None,
        'results': [
            {
                'model': result.model,
                'identity': result.identity,
                'params': dict(result.params),
                'tuning': asdict(result.tuning),
                'evaluation': {group: asdict(scores) for group, scores in breakdown(result.predictions).items()},
            }
            for result in results
        ],
    }
    (folder / EVALUATION_JSON).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
    with (folder / CALIBRATION_CSV).open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        writer.writerow(['model', 'identity', *(column.name for column in fields(CalibrationBin))])
        for result in results:
            for row in calibration(result.predictions):
                values = (round(value, REPORT_DIGITS) for value in astuple(row))
                writer.writerow([result.model, result.identity, *values])


if __name__ == '__main__':
    sys.exit(main())
