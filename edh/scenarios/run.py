"""Evaluate the league play scenarios of one or several leagues.

Importing this module has no side effect; run `python -m edh.scenarios.run <league> [<league> ...]`, where each
`<league>` is a folder of `_data/` holding a `league.json`, or a pattern matching several (`'rlcs_2027_*'`). Leagues
run one after the other with the same options; one that fails is logged and the others still run (exit code 1 if
any failed). With the Liquipedia source (default), the fetched results and event details are also saved as
`results.csv` and `event.json`, which the `csv` source reads back offline, and the competition and team logos are
downloaded to the league's local `logos/` folder. The outlook (and the cards) of each run is saved in
`outlook/<YYYY-MM-DD>/<method>/`, dated by the league's progress (the last day a series finished, in the event's
timezone, also the date on the card), so the outlooks of different matchdays and methods are kept side by side (a
rerun of the same method on the same results replaces its own). `--visual` also renders the outlook card (PNG, every
social format); `--visual-only` renders it from the latest saved outlook (of `--method`, unless `auto`) without
evaluating.
"""

# Standard library
import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path

# Local
from .. import paths
from ..exceptions import ConfigurationError, DataSourceError, EsportsDataHubError
from .config import league_path, load_league
from .constants import (
    DEFAULT_SAMPLES,
    EVENT_JSON,
    LEAGUE_JSON,
    LOGOS_DIR,
    METHOD_AUTO,
    METHODS,
    OUTLOOK_DIR,
    OUTLOOK_JSON,
    RESULTS_CSV,
    SOURCE_CSV,
    SOURCE_LIQUIPEDIA,
    SOURCES,
)
from .engine import evaluate
from .logos import download_logos, read_logos
from .models import EvaluationOptions, EventInfo, LeagueConfig, ScenarioReport, Series
from .report import format_table, read_outlook, write_outlook
from .sources import CsvSource, LiquipediaSource, ResultSource, write_event, write_results
from .visuals.export import export_cards

logger = logging.getLogger(__name__)

GLOB_CHARS = frozenset('*?[')  # a league name holding one of these is a pattern over `_data/`


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: Arguments (defaults to `sys.argv[1:]`).

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(prog='python -m edh.scenarios.run', description=__doc__.splitlines()[0])
    parser.add_argument(
        'leagues',
        nargs='+',
        metavar='league',
        help="league folder in _data/ (e.g. cdf_slash_2026), or a pattern (e.g. 'rlcs_2027_*'); several allowed",
    )
    parser.add_argument('--source', choices=SOURCES, default=SOURCE_LIQUIPEDIA, help='where results come from')
    parser.add_argument('--method', choices=METHODS, default=METHOD_AUTO, help='evaluation method')
    parser.add_argument('--samples', type=int, default=DEFAULT_SAMPLES, help='Monte Carlo sample count')
    parser.add_argument('--seed', type=int, default=None, help='Monte Carlo random seed')
    parser.add_argument('--workers', type=int, default=None, help='worker processes (default: one per CPU)')
    visual = parser.add_mutually_exclusive_group()
    visual.add_argument('--visual', action='store_true', help='also render the outlook card (PNG, every format)')
    visual.add_argument(
        '--visual-only',
        action='store_true',
        help='only render the outlook card, from the latest saved outlook.json (of --method, unless auto)',
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


def resolve_leagues(names: Sequence[str]) -> list[str]:
    """Expand the leagues named on the command line into league folders.

    Args:
        names: League folders of `_data/`, or patterns (`*`, `?`, `[...]`) matching the folders holding a
            `league.json`.

    Returns:
        The league folders, in command line order (each pattern's matches sorted), without duplicates. A plain
        name is kept as is: a missing league fails when it runs.

    Raises:
        ConfigurationError: If a pattern matches no league.
    """
    leagues: list[str] = []
    for name in names:
        if not any(char in name for char in GLOB_CHARS):
            leagues.append(name)
            continue
        matches = sorted(path.parent.name for path in paths.DATA_DIR.glob(f'{name}/{LEAGUE_JSON}'))
        if not matches:
            raise ConfigurationError(f'No league matches {name!r} in {paths.DATA_DIR}')
        leagues.extend(matches)
    return list(dict.fromkeys(leagues))


def main(argv: Sequence[str] | None = None) -> int:
    """Run each league named on the command line, then sum up which ones failed.

    Args:
        argv: Arguments (defaults to `sys.argv[1:]`).

    Returns:
        The exit code: 0 when every league ran, 1 when one failed (or a pattern matched nothing).
    """
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    # one INFO line per request otherwise: httpx2 (and its httpcore2) for the API calls and the logo downloads
    for name in ('httpx2', 'httpcore2'):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        leagues = resolve_leagues(args.leagues)
    except EsportsDataHubError as error:
        logger.critical('%s', error)
        return 1
    failed = [league for league in leagues if not _run_league(league, args)]
    if len(leagues) > 1:
        logger.info('%d of %d leagues ran%s', len(leagues) - len(failed), len(leagues), _failed_note(failed))
    return 1 if failed else 0


def _failed_note(failed: Sequence[str]) -> str:
    """Name the failed leagues for the summary line (empty when none failed)."""
    return f'; failed: {", ".join(failed)}' if failed else ''


def _run_league(league: str, args: argparse.Namespace) -> bool:
    """Fetch the results of one league, evaluate it, print and export the outlook, render the card if asked.

    Returns whether it ran; an error is logged with the league's name instead of being raised, so the next league
    still runs.
    """
    try:
        config = load_league(league)
        if args.visual_only:
            report, as_of = _saved_outlook(config, args.method)
            event = CsvSource.fetch_event(config)
        else:
            report, event, as_of = _evaluate(args, config)
        if args.visual or args.visual_only:
            event = replace(event, logos=read_logos(league_path(config, LOGOS_DIR)))
            cards = export_cards(report, config, event, outlook_dir(config, as_of, report.method), as_of=as_of)
            logger.info('Outlook card saved to %s', ', '.join(str(path) for path in cards))
    except EsportsDataHubError as error:
        logger.critical('%s: %s', league, error)
        return False
    return True


def outlook_dir(config: LeagueConfig, day: date, method: str) -> Path:
    """Folder of the outlook (and cards) of a run.

    Args:
        config: League evaluated.
        day: Day the results are as of (`league_day`).
        method: Method actually used (`auto` resolved).

    Returns:
        `_data/<league>/outlook/<YYYY-MM-DD>/<method>/`.
    """
    return league_path(config, OUTLOOK_DIR, day.isoformat(), method)


def league_day(series: Sequence[Series]) -> date:
    """Return the day the league's results are as of: the last day a series finished, in the event's timezone.

    Args:
        series: League series (`played_on` set by the source).

    Returns:
        The latest `played_on` of the finished series; today when none is known (nothing played yet, or a snapshot
        without dates).
    """
    days: list[date] = []
    for item in series:
        day = item.played_on
        if item.finished and day is not None:
            days.append(day)
    if not days:
        logger.warning('No finished series with a known day: results dated today')
        return date.today()
    return max(days)


def _evaluate(args: argparse.Namespace, config: LeagueConfig) -> tuple[ScenarioReport, EventInfo, date]:
    """Fetch the results and event details, evaluate the scenarios, print the table and export the outlook.

    Returns the report, the event details and the day the results are as of.
    """
    source = build_source(args.source)
    series = source.fetch(config)
    event = source.fetch_event(config)
    day = league_day(series)
    if args.source == SOURCE_LIQUIPEDIA:
        write_results(series, league_path(config, RESULTS_CSV))
        write_event(event, league_path(config, EVENT_JSON))
        download_logos(event, league_path(config, LOGOS_DIR))
    options = EvaluationOptions(method=args.method, samples=args.samples, seed=args.seed, workers=args.workers)
    report = evaluate(config, series, options)
    sys.stdout.write(format_table(report))
    csv_path, json_path = write_outlook(report, outlook_dir(config, day, report.method))
    logger.info('Outlook saved to %s and %s', csv_path, json_path)
    return report, event, day


def _saved_outlook(config: LeagueConfig, method: str) -> tuple[ScenarioReport, date]:
    """Read the league's latest saved outlook (of `method`, any method for `auto`), dated by its folder.

    The latest day wins; between methods saved on that day, the last written.
    """
    pattern = f'*/{"*" if method == METHOD_AUTO else method}/{OUTLOOK_JSON}'
    saved = []
    for path in league_path(config, OUTLOOK_DIR).glob(pattern):
        try:
            day = date.fromisoformat(path.parent.parent.name)
        except ValueError:
            continue  # not a run folder
        saved.append((day, path.stat().st_mtime, path))
    if not saved:
        kind = 'outlook' if method == METHOD_AUTO else f'{method} outlook'
        raise DataSourceError(f'No saved {kind} for {config.slug} (evaluate the league first)')
    day, _, path = max(saved)
    return read_outlook(path), day


if __name__ == '__main__':
    sys.exit(main())
