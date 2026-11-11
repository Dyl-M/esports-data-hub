"""Fetch the LPDB match history into the local cache and summarize it.

An update fetches what the cache misses (from a few weeks before the last day synced); `--refetch` fetches everything
again. LPDB rate-limits bulk reads: a run stopped by it keeps what arrived, and the next run resumes.
"""

# Standard library
import argparse
import datetime as dt
import logging
import sys
from collections import Counter
from collections.abc import Sequence

# Local
from ..exceptions import EsportsDataHubError
from ..lpdb import open_client
from .constants import HISTORY_MAX_RETRIES, HISTORY_RETRY_BACKOFF, TEAM_KINDS
from .history import read_history, update_history
from .lineups import identify_lineups, season_of

logger = logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: Arguments, without the program name; `sys.argv[1:]` when None.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(prog='python -m edh.predictions.run', description=__doc__.splitlines()[0])
    parser.add_argument('--refetch', action='store_true', help='fetch the whole history again')
    parser.add_argument('--offline', action='store_true', help='only summarize the cached history, fetch nothing')
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Update the history, then log what it holds.

    Args:
        argv: Arguments, without the program name; `sys.argv[1:]` when None.

    Returns:
        The exit code: 0 on success, 1 on error.
    """
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    for name in ('httpx', 'httpcore'):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        if not args.offline:
            with open_client(max_retries=HISTORY_MAX_RETRIES, retry_backoff_factor=HISTORY_RETRY_BACKOFF) as client:
                update_history(client, today=dt.datetime.now(dt.UTC).date(), refetch=args.refetch)
        matches = read_history()
    except EsportsDataHubError as error:
        logger.error('%s', error)
        return 1
    per_season = Counter(season_of(match.played_at.date()) for match in matches)
    teams = {opponent.name for match in matches for opponent in match.opponents}
    logger.info(
        'Cached history: %d finished team matches, %d teams (%s)',
        len(matches),
        len(teams),
        ', '.join(f'RLCS {season}: {count}' for season, count in sorted(per_season.items())),
    )
    lineups = identify_lineups(matches).by_id
    kinds = Counter(lineup.kind for lineup in lineups)
    logger.info(
        'Lineups: %d, %d from a predecessor (%s)',
        len(lineups),
        sum(lineup.predecessor is not None for lineup in lineups),
        ', '.join(f'{kind}: {kinds[kind]}' for kind in TEAM_KINDS),
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
