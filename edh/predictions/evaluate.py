"""Evaluate the rating baselines on the cached match history.

Each model is tuned for each identity on the tuning season (a grid, refined by a bounded search), then the evaluation
season is scored once. The comparison is logged and saved to `_data/predictions/<last match day>/` (`evaluation.json`,
`calibration.csv`).
"""

# Standard library
import argparse
import csv
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict, astuple, fields
from itertools import product
from pathlib import Path
from typing import Any

# Local
from .. import paths
from ..exceptions import EsportsDataHubError
from .constants import CALIBRATION_CSV, EVALUATION_JSON, IDENTITIES, PREDICTIONS_FOLDER, REPORT_DIGITS
from .evaluation import CalibrationBin, breakdown, calibration, first_team_share, score
from .history import read_history
from .lineups import identify_lineups
from .models import PlayedMatch
from .tuning import MODELS, Result, evaluate_all

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
    parser.add_argument('--workers', type=positive_int, default=None, help='worker processes (default: one per CPU)')
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    """Return a count of at least 1, from the command line.

    Args:
        value: The option's text.

    Returns:
        The count.

    Raises:
        ArgumentTypeError: If it is below 1.
    """
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError(f'must be at least 1, got {count}')
    return count


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
        tasks = list(product(args.models, args.identities))
        results = evaluate_all(matches, identify_lineups(matches), tasks, workers=args.workers)
    except EsportsDataHubError as error:
        logger.error('%s', error)
        return 1
    for line in summary(results):
        logger.info('%s', line)
    folder = report_folder(matches)
    write_report(results, folder)
    logger.info('Saved to %s', folder)
    return 0


def report_folder(matches: Sequence[PlayedMatch]) -> Path:
    """Return the folder of the reports on a history: `_data/predictions/<last match day>/`.

    Args:
        matches: The history (at least one match).

    Returns:
        The folder, named after the day of its latest match.
    """
    return paths.DATA_DIR / PREDICTIONS_FOLDER / max(match.played_at for match in matches).date().isoformat()


def summary(results: Sequence[Result]) -> list[str]:
    """Lines of the comparison table: series log loss, Brier score, accuracy and game log loss of the evaluation season.

    Args:
        results: Tuned models, all on the same seasons.

    Returns:
        A header line (when there is a result), one line per model and identity (best series log loss first; a
        hyperparameter picked on a bound of its search is starred), then the share of the series won by the team
        listed first.
    """
    if not results:
        return []
    seasons = results[0].seasons
    lines = [
        f'RLCS {seasons.evaluation} (tuned on RLCS {seasons.tuning}): '
        'model / identity / log loss / Brier / accuracy / game log loss / runs / parameters (* on a bound)'
    ]
    scored = sorted(((score(result.predictions), result) for result in results), key=lambda pair: pair[0].log_loss)
    for scores, result in scored:
        params = ', '.join(_parameter(name, value, result.at_bound) for name, value in result.params.items()) or '-'
        lines.append(
            f'{result.model:<14} {result.identity:<8} {scores.log_loss:.4f} {scores.brier:.4f} '
            f'{scores.accuracy:.3f} {scores.game_log_loss:.4f} {len(result.trials):>4}  {params}'
        )
    lines.append(f'The first team listed won {first_team_share(results[0].predictions):.1%} of these series')
    return lines


def _parameter(name: str, value: float, at_bound: Sequence[str]) -> str:
    """A hyperparameter as `name=value`, starred when picked on a bound of its search."""
    return f'{name}={value:g}' + '*' * (name in at_bound)


def write_report(results: Sequence[Result], folder: Path) -> None:
    """Save the evaluation: scores per group in `evaluation.json`, calibration bins in `calibration.csv`.

    Args:
        results: Tuned models, all on the same seasons (at least one).
        folder: Output folder, created when missing.
    """
    write_json(report(results), folder / EVALUATION_JSON)
    with (folder / CALIBRATION_CSV).open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        writer.writerow(['model', 'identity', *(column.name for column in fields(CalibrationBin))])
        for result in results:
            for row in calibration(result.predictions):
                values = (round(value, REPORT_DIGITS) for value in astuple(row))
                writer.writerow([result.model, result.identity, *values])


def report(results: Sequence[Result]) -> dict[str, Any]:
    """Return a tuning's report: seasons, first team share, and per model and identity its picks and scores.

    Args:
        results: Tuned models, all on the same seasons (at least one).

    Returns:
        The report, ready for JSON.
    """
    seasons = results[0].seasons
    return {
        'tuning_season': seasons.tuning,
        'evaluation_season': seasons.evaluation,
        'first_team_share': first_team_share(results[0].predictions),
        'results': [
            {
                'model': result.model,
                'identity': result.identity,
                'params': dict(result.params),
                'at_bound': list(result.at_bound),
                'tuning': asdict(result.tuning),
                'evaluation': {group: asdict(scores) for group, scores in breakdown(result.predictions).items()},
                'trials': [{'params': dict(trial.params), 'log_loss': trial.log_loss} for trial in result.trials],
            }
            for result in results
        ],
    }


def write_json(content: dict[str, Any], path: Path) -> None:
    """Write a report as indented JSON with a final newline, creating its folder when missing.

    Args:
        content: The report.
        path: File to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2) + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    sys.exit(main())
