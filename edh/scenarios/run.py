"""Evaluate the league play scenarios of a league.

Importing this module has no side effect; run `python -m edh.scenarios.run <league>`, where `<league>` is a folder
of `_data/` holding a `league.json`. With the Liquipedia source (default), the fetched results and event details are
also saved as `results.csv` and `event.json`, which the `csv` source reads back offline. `--visual` also renders the
outlook card (PNG, every social format); `--visual-only` renders it from the saved outlook without evaluating.
"""

# Standard library
import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import date

# Local
from ..exceptions import EsportsDataHubError
from .config import league_dir, load_league
from .constants import (
    DEFAULT_SAMPLES,
    METHOD_AUTO,
    METHODS,
    OUTLOOK_DIR,
    OUTLOOK_JSON,
    SOURCE_CSV,
    SOURCE_LIQUIPEDIA,
    SOURCES,
)
from .engine import evaluate
from .models import EvaluationOptions, EventInfo, LeagueConfig, ScenarioReport
from .report import format_table, read_outlook, write_outlook
from .sources import CsvSource, LiquipediaSource, ResultSource, event_path, results_path, write_event, write_results
from .visuals.export import export_cards

logger = logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: Arguments (defaults to `sys.argv[1:]`).

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(prog='python -m edh.scenarios.run', description=__doc__.splitlines()[0])
    parser.add_argument('league', help='league folder in _data/ (e.g. cdf_slash_2026)')
    parser.add_argument('--source', choices=SOURCES, default=SOURCE_LIQUIPEDIA, help='where results come from')
    parser.add_argument('--method', choices=METHODS, default=METHOD_AUTO, help='evaluation method')
    parser.add_argument('--samples', type=int, default=DEFAULT_SAMPLES, help='Monte Carlo sample count')
    parser.add_argument('--seed', type=int, default=None, help='Monte Carlo random seed')
    parser.add_argument('--workers', type=int, default=None, help='worker processes (default: one per CPU)')
    visual = parser.add_mutually_exclusive_group()
    visual.add_argument('--visual', action='store_true', help='also render the outlook card (PNG, every format)')
    visual.add_argument(
        '--visual-only', action='store_true', help='only render the outlook card, from the saved outlook.json'
    )
    return parser.parse_args(argv)


def build_source(name: str) -> ResultSource:
    """Create the result source named on the command line.

    Args:
        name: `liquipedia` or `csv` (both locate the league from the config passed to `fetch`).

    Returns:
        The result source.
    """
    if name == SOURCE_CSV:
        return CsvSource()
    return LiquipediaSource()


def main(argv: Sequence[str] | None = None) -> int:
    """Fetch the results, evaluate the scenarios, print and export the outlook, render the card if asked.

    Args:
        argv: Arguments (defaults to `sys.argv[1:]`).

    Returns:
        The exit code: 0 on success, 1 on error.
    """
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)  # one INFO line per API request otherwise
    try:
        config = load_league(args.league)
        if args.visual_only:
            report, as_of = _saved_outlook(config)
            event = CsvSource.fetch_event(config)
        else:
            report, event = _evaluate(args, config)
            as_of = date.today()
        if args.visual or args.visual_only:
            cards = export_cards(report, config, event, league_dir(config.slug) / OUTLOOK_DIR, as_of=as_of)
            logger.info('Outlook card saved to %s', ', '.join(str(path) for path in cards))
    except EsportsDataHubError as error:
        logger.critical('%s', error)
        return 1
    return 0


def _evaluate(args: argparse.Namespace, config: LeagueConfig) -> tuple[ScenarioReport, EventInfo]:
    """Fetch the results and event details, evaluate the scenarios, print the table and export the outlook."""
    source = build_source(args.source)
    series = source.fetch(config)
    event = source.fetch_event(config)
    if args.source == SOURCE_LIQUIPEDIA:
        write_results(series, results_path(config))
        write_event(event, event_path(config))
    options = EvaluationOptions(method=args.method, samples=args.samples, seed=args.seed, workers=args.workers)
    report = evaluate(config, series, options)
    sys.stdout.write(format_table(report))
    csv_path, json_path = write_outlook(report, league_dir(config.slug) / OUTLOOK_DIR)
    logger.info('Outlook saved to %s and %s', csv_path, json_path)
    return report, event


def _saved_outlook(config: LeagueConfig) -> tuple[ScenarioReport, date]:
    """Read the league's saved outlook, dated by the day it was written."""
    path = league_dir(config.slug) / OUTLOOK_DIR / OUTLOOK_JSON
    report = read_outlook(path)
    return report, date.fromtimestamp(path.stat().st_mtime)


if __name__ == '__main__':
    sys.exit(main())
