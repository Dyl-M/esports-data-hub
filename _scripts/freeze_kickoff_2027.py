"""Freeze the forecasts of every pair of the RLCS 2027 Kick-Off's teams before it starts (Dec 9-13, 2026, London).

Run once every slot is filled on Liquipedia (the four Open winners included) and the cache is refreshed
(`uv run python -m edh.predictions.run`), before December 9: `uv run python -m _scripts.freeze_kickoff_2027`.
Writes `_data/predictions/kickoff_2027/frozen.json`, never overwritten; commit it so its date shows it came first.
"""

# Standard library
import datetime as dt
import logging
import sys
from pathlib import Path

# Local
from edh import paths
from edh.exceptions import EsportsDataHubError
from edh.lpdb import open_client
from edh.predictions.constants import FROZEN_JSON, PREDICTIONS_FOLDER
from edh.predictions.freeze import freeze

PAGE = 'Rocket_League_Championship_Series/Club_Championship/2027/Kick-Off'
FIRST_DAY = dt.date(2026, 12, 9)
FOLDER = 'kickoff_2027'

logger = logging.getLogger(__name__)


def frozen_path() -> Path:
    """Return the Kick-Off's frozen file.

    Returns:
        `_data/predictions/kickoff_2027/frozen.json`.
    """
    return paths.DATA_DIR / PREDICTIONS_FOLDER / FOLDER / FROZEN_JSON


def main() -> int:
    """Freeze the Kick-Off's forecasts and log what was saved.

    Returns:
        The exit code: 0 on success, 1 on error (the file already frozen included).
    """
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    for name in ('httpx2', 'httpcore2'):
        logging.getLogger(name).setLevel(logging.WARNING)
    path = frozen_path()
    try:
        with open_client() as client:
            content = freeze(client, PAGE, FIRST_DAY, path)
    except EsportsDataHubError as error:
        logger.error('%s', error)
        return 1
    logger.info(
        'Froze %d teams, %d pairs (history through %s): %s',
        len(content['teams']),
        len(content['pairs']),
        content['history_through'],
        path,
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
