"""Forecast matches not played yet between given teams.

Each pair gets a per-game and a series win probability. `--tune` picks the forecasts' hyperparameters on the season
before the forecast's day (every model and identity asked for) and saves them to
`_data/predictions/<last match day>/forecast_params.json`, merged with the picks already saved there; forecasts take the
latest picks of each model and identity. Every pair of the teams given is then predicted, for each model and identity,
with the history before the forecast's day.
"""

# Standard library
import argparse
import datetime as dt
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from itertools import combinations, product
from pathlib import Path
from typing import Any

# Local
from .. import paths
from ..exceptions import DataSourceError, EsportsDataHubError
from .constants import FORECAST_BEST_OF, FORECAST_JSON, IDENTITIES, PREDICTIONS_FOLDER
from .evaluate import positive_int, report, report_folder, summary, write_json
from .forecast import Forecast, Forecaster, Side
from .history import read_history
from .lineups import identify_lineups
from .models import Lineups, PlayedMatch
from .tuning import MODELS, Params, Seasons, evaluate_all, forecast_seasons, played_seasons

logger = logging.getLogger(__name__)

Task = tuple[str, str]  # a model and an identity
Picks = dict[Task, Params]  # hyperparameters by model and identity
Pair = tuple[str, str, dict[Task, Forecast]]  # two teams and their forecasts by model and identity

# === Command line ===


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: Arguments, without the program name; `sys.argv[1:]` when None.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(prog='python -m edh.predictions.predict', description=__doc__.splitlines()[0])
    parser.add_argument(
        'teams', nargs='*', help='teams to forecast, as Liquipedia names them (every pair is predicted)'
    )
    parser.add_argument('--tune', action='store_true', help='pick the hyperparameters again first')
    parser.add_argument(
        '--roster',
        action='append',
        type=_roster,
        default=[],
        metavar='TEAM=PLAYER,...',
        help='roster of a team, as player pages (replaces its latest one in the history; repeatable)',
    )
    today = _start_of(dt.datetime.now(dt.UTC).date())
    parser.add_argument('--when', type=_day, default=today, help='day of the matches, YYYY-MM-DD (default: today)')
    parser.add_argument('--best-of', type=_best_of, default=FORECAST_BEST_OF, help='series length quoted')
    parser.add_argument('--models', nargs='+', choices=list(MODELS), default=list(MODELS), help='models')
    parser.add_argument('--identities', nargs='+', choices=IDENTITIES, default=list(IDENTITIES), help='identities')
    parser.add_argument(
        '--workers', type=positive_int, default=None, help='tuning worker processes (default: all CPUs)'
    )
    args = parser.parse_args(argv)
    if len(args.teams) == 1 or not (args.teams or args.tune):
        parser.error('give at least two teams (or none with --tune)')
    rosters: list[tuple[str, tuple[str, ...]]] = args.roster
    stray = sorted({team for team, _ in rosters} - set(args.teams))
    if stray:
        parser.error(f'--roster for teams not forecast: {", ".join(stray)}')
    return args


def _roster(value: str) -> tuple[str, tuple[str, ...]]:
    """A team and its players, from `TEAM=PLAYER,PLAYER,...`."""
    team, separator, players = value.partition('=')
    roster = tuple(player.strip() for player in players.split(',') if player.strip())
    if not (separator and team.strip() and roster):
        raise argparse.ArgumentTypeError(f'expected TEAM=PLAYER,PLAYER,..., got {value!r}')
    return team.strip(), roster


def _day(value: str) -> dt.datetime:
    """The start of a day in UTC, from `YYYY-MM-DD`."""
    return _start_of(dt.date.fromisoformat(value))


def _start_of(day: dt.date) -> dt.datetime:
    """The start of a day in UTC."""
    return dt.datetime.combine(day, dt.time(), dt.UTC)


def _best_of(value: str) -> int:
    """An odd series length, from the command line."""
    length = int(value)
    if max(length, 0) % 2 == 0:  # even, or not positive (counted as 0, even)
        raise argparse.ArgumentTypeError(f'must be an odd number of games, got {length}')
    return length


def main(argv: Sequence[str] | None = None) -> int:
    """Tune when asked, then forecast every pair of the teams given and log the probabilities.

    Args:
        argv: Arguments, without the program name; `sys.argv[1:]` when None.

    Returns:
        The exit code: 0 on success, 1 on error.
    """
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    tasks = list(product(args.models, args.identities))
    try:
        matches = read_history()
        lineups = identify_lineups(matches)
        if args.tune:
            tune(matches, lineups, tasks, args.workers, args.when.date())
        if not args.teams:
            return 0
        rosters = dict(args.roster)
        teams = {team: rosters.get(team) for team in args.teams}
        forecasts = forecast_all(matches, lineups, _select(latest_picks(), tasks), args.when, teams)
    except EsportsDataHubError as error:
        logger.error('%s', error)
        return 1
    logger.info(
        'Forecasts for %s, history through %s', args.when.date(), max(match.played_at for match in matches).date()
    )
    for line in forecast_lines(forecasts, args.best_of):
        logger.info('%s', line)
    return 0


# === Tuning ===


def tune(
    matches: Sequence[PlayedMatch], lineups: Lineups, tasks: Sequence[Task], workers: int | None, day: dt.date
) -> None:
    """Pick the forecasts' hyperparameters on the season before the day's, log and save them.

    The picks are checked on the day's season; before its first played match, on the tuning season itself (in-sample,
    with a warning).

    Args:
        matches: The history.
        lineups: Lineup identity over the same history.
        tasks: Model name and identity of each tuning.
        workers: Worker processes (None: one per CPU).
        day: Day of the forecasts.
    """
    seasons = forecast_seasons(day)
    if seasons.evaluation not in played_seasons(matches):
        logger.warning(
            'No played match of RLCS %d yet: the picks are checked on RLCS %d, the season they are tuned on',
            seasons.evaluation,
            seasons.tuning,
        )
        seasons = Seasons(seasons.tuning, seasons.tuning)
    results = evaluate_all(matches, lineups, tasks, seasons, workers)
    for line in summary(results):
        logger.info('%s', line)
    path = report_folder(matches) / FORECAST_JSON
    write_json(_merged(report(results), path), path)
    logger.info('Saved to %s', path)


def _merged(content: dict[str, Any], path: Path) -> dict[str, Any]:
    """A tuning's report with the other models and identities already saved at path on the same seasons kept."""
    if not path.exists():
        return content
    saved: dict[str, Any] = json.loads(path.read_text(encoding='utf-8'))
    if (saved['tuning_season'], saved['evaluation_season']) != (content['tuning_season'], content['evaluation_season']):
        return content
    results = {_task(result): result for result in saved['results']}
    results.update({_task(result): result for result in content['results']})
    return content | {'results': list(results.values())}


def _task(result: Mapping[str, Any]) -> Task:
    """The model and identity of a saved result."""
    return result['model'], result['identity']


def latest_picks() -> Picks:
    """Return the latest hyperparameters of each model and identity among the saved forecasts' tunings.

    Returns:
        The picks, by model and identity.

    Raises:
        DataSourceError: If no tuning was saved yet.
    """
    saved = sorted((paths.DATA_DIR / PREDICTIONS_FOLDER).glob(f'*/{FORECAST_JSON}'))
    if not saved:
        raise DataSourceError('No forecast tuning saved yet: run `python -m edh.predictions.predict --tune`')
    picks: Picks = {}
    for path in saved:
        content: dict[str, Any] = json.loads(path.read_text(encoding='utf-8'))
        picks.update({_task(result): result['params'] for result in content['results']})
    return picks


def _select(picks: Picks, tasks: Sequence[Task]) -> Picks:
    """The picks of the tasks asked for; an error naming those never tuned."""
    missing = [f'{model} / {identity}' for model, identity in tasks if (model, identity) not in picks]
    if missing:
        raise DataSourceError(f'No tuned hyperparameters for {", ".join(missing)}: run with --tune')
    return {task: picks[task] for task in tasks}


# === Forecasts ===


def forecast_all(
    matches: Sequence[PlayedMatch],
    lineups: Lineups,
    picks: Picks,
    when: dt.datetime,
    teams: Mapping[str, Sequence[str] | None],
) -> list[Pair]:
    """Forecast every pair of the teams, with each model and identity.

    Args:
        matches: The history.
        lineups: Lineup identity over the same history.
        picks: Hyperparameters of each model and identity to run.
        when: Time of the matches.
        teams: The teams in order, each with its roster (player pages; None for its latest one in the history).

    Returns:
        Every pair, in the teams' order: its two teams and its forecasts by model and identity.
    """
    forecasters = {
        (model, identity): Forecaster(matches, lineups, identity, MODELS[model].factory(**params), when)
        for (model, identity), params in picks.items()
    }
    return [
        (
            first,
            second,
            {
                task: forecaster.forecast(forecaster.side(first, teams[first]), forecaster.side(second, teams[second]))
                for task, forecaster in forecasters.items()
            },
        )
        for first, second in combinations(teams, 2)
    ]


def forecast_lines(pairs: Sequence[Pair], best_of: int) -> list[str]:
    """Lines of the forecasts: per pair, one line per model and identity, with the members never rated.

    Args:
        pairs: The pairs' teams and forecasts by model and identity.
        best_of: Series length quoted.

    Returns:
        A header line per pair, then its forecasts.
    """
    lines = []
    for first, second, forecasts in pairs:
        lines.append(f'{first} vs {second}: model / identity / game / best of {best_of}')
        lines.extend(
            f'  {model:<14} {identity:<8} {forecast.game_probability:.3f} '
            f'{forecast.series_probability(best_of // 2 + 1):.3f}{_unseen(forecast)}'
            for (model, identity), forecast in forecasts.items()
        )
    return lines


def _unseen(forecast: Forecast) -> str:
    """The members of a forecast the history never rated, as a suffix (empty when there is none)."""
    parts = [part for part in map(_unseen_side, (forecast.first, forecast.second), forecast.unseen) if part]
    return f'  never rated: {"; ".join(parts)}' if parts else ''


def _unseen_side(side: Side, members: Sequence[str]) -> str:
    """The members of one side never rated: the team when it is new, else its players; empty when there is none."""
    if not members:
        return ''
    if tuple(members) == (side.team,):
        return f'{side.team} (new team)'
    return f'{side.team}: {", ".join(members)}'


if __name__ == '__main__':
    sys.exit(main())
