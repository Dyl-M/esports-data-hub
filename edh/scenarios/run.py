"""Evaluate the league play scenarios of a league.

Importing this module has no side effect; run `python -m edh.scenarios.run <league>`, where `<league>` is a folder
of `_data/` holding a `league.json`. With the Liquipedia source (default), the fetched results are also saved as
`results.csv`, which the `csv` source reads back offline.
"""

# Standard library
import argparse
import logging
import sys
from collections.abc import Sequence

# Local
from ..exceptions import EsportsDataHubError
from .config import league_dir, load_league
from .constants import (
    DEFAULT_SAMPLES,
    METHOD_AUTO,
    METHODS,
    OUTLOOK_DIR,
    SOURCE_CSV,
    SOURCE_LIQUIPEDIA,
    SOURCES,
)
from .engine import evaluate
from .report import format_table, write_outlook
from .sources import CsvSource, LiquipediaSource, ResultSource, results_path, write_results

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
    """Fetch the results, evaluate the scenarios, print and export the outlook.

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
        series = build_source(args.source).fetch(config)
        if args.source == SOURCE_LIQUIPEDIA:
            write_results(series, results_path(config))
        report = evaluate(config, series, method=args.method, samples=args.samples, seed=args.seed)
    except EsportsDataHubError as error:
        logger.critical('%s', error)
        return 1
    sys.stdout.write(format_table(report))
    csv_path, json_path = write_outlook(report, league_dir(config.slug) / OUTLOOK_DIR)
    logger.info('Outlook saved to %s and %s', csv_path, json_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
