# Rating predictions (`edh.predictions`)

Per-game win probabilities learned from past results, meant to replace the "every score is equally likely" model of
the [league play scenarios](/edh/scenarios/README.md). It holds the **match history**, fetched from Liquipedia and
cached locally, the **lineups** a rating follows, the **rating baselines** (uniform, Elo, Glicko-2, Bradley–Terry)
with their walk-forward evaluation, and **forecasts** of matches not played yet. How it works:
[`_docs/predictions.md`](/_docs/predictions.md).

## Contents

- [Setup](#setup)
- [Match history](#match-history)
- [Running](#running)
- [Reading the history](#reading-the-history)
- [Lineups](#lineups)
- [Rating baselines](#rating-baselines)
- [Forecasts](#forecasts)
- [Module map](#module-map)

## Setup

The [shared setup](/README.md#setup), including an LPDB API key. Nothing else: the history needs no extra dependency.

## Match history

| Scope  | Value                                                                                               |
|--------|-----------------------------------------------------------------------------------------------------|
| Game   | Rocket League (Liquipedia wiki `rocketleague`)                                                      |
| Period | From September 1, 2023 (`HISTORY_START`): RLCS 2024, 2025, 2026 and 2027 so far                     |
| Tiers  | Liquipedia tiers 1 and 2 (`HISTORY_TIERS`)                                                          |
| Mode   | 3v3 when read (`TEAM_MODE`); other modes (1v1, 2v2, a few 4v4 show matches) are cached but left out |

RLCS 2024 warms the ratings up, RLCS 2025 tunes them, RLCS 2026 evaluates them, and RLCS 2027 is what they will
predict.

**Cache.** Matches are stored in `.cache/lpdb/<wiki>/` (gitignored, about 3.4 KB per match, some 86 MB for the scope
in November 2026):

- `pages/<page>.json`: one file per tournament page (its name percent-encoded), holding LPDB match records restricted
  to the fields needed (`MATCH_FIELDS`), with the time they were fetched. Records are parsed when read, so a parser
  change needs no refetch.
- `manifest.json`: the scope and the last day synced (`synced_through`).

**Updates.** The whole scope is fetched as one query (by date and tier, 1000 matches per request), since LPDB
rate-limits bulk reads and a few large requests go further than hundreds of small ones. Each page of results is merged
into the cache as it arrives (a refetched match replaces the cached one):

- A later run fetches again from 30 days before `synced_through` (`REFETCH_DAYS`): results get entered or edited late.
- A changed scope, or `--refetch`, starts over from the scope's start.
- Any LPDB error stops the run with what arrived kept: run it again to resume. Rate-limited requests are retried with
  growing waits first (`HISTORY_MAX_RETRIES`, `HISTORY_RETRY_BACKOFF`). LPDB sends no `Retry-After` hint and its limit
  isn't documented: after a lockout, wait (about 20 minutes was enough) rather than retrying in a loop.

## Running

```bash
uv run python -m edh.predictions.run               # update the cache, then summarize it
uv run python -m edh.predictions.run --offline     # summarize the cache only, no request
uv run python -m edh.predictions.run --refetch     # fetch the whole scope again
```

The summary gives the finished 3v3 matches, the teams, the matches per RLCS season and the lineups.

## Reading the history

```python
from edh.predictions.history import read_history

matches = read_history()  # finished 3v3 matches of the default scope, oldest first
```

Each `PlayedMatch` has its LPDB id and page, its start time in UTC, the tier, whether it was played on LAN (None when
unknown), the two `Opponent`s (team name, team template of the period, series score, roster as `Player`s whose
Liquipedia page is their identity across teams) and the goals of each decided game. Walkovers are kept, flagged,
scored 1-0 and without games. Matches not finished, without a date, without two named teams, or without a winner are
left out.

Team names are used as Liquipedia gives them: a renamed team appears under each of its names. A team whose icon is a
flag is marked `national`.

## Lineups

Ratings follow lineups, a team name with a core of players kept together, rather than names (the rules and why they
are built this way: [How it works](/_docs/predictions.md#lineup-identity)):

```python
from edh.predictions.history import read_history
from edh.predictions.lineups import identify_lineups, season_of

matches = read_history()
lineups = identify_lineups(matches)  # computed on the fly (a fraction of a second), nothing cached
first, second = lineups.of(matches[-1])  # the Lineup of each opponent
season_of(matches[-1].played_at.date())  # RLCS season of a day, e.g. 2027
```

Each `Lineup` has its id (its index in `lineups.by_id`, in order of first match), its team name, its kind (`club`,
`national` or `college`), the start of its first match and its predecessor's id (None when it took its core from no
earlier lineup). `lineups.rosters` gives, per match, the players of each side: the recorded roster, or the lineup's
latest one when too few players were recorded.

The rules' settings are in `constants.py`, *Lineup identity* (`CORE_PLAYERS`, `CARRYOVER_DAYS`, `COLLEGE_PAGE_ROOTS`,
`SEASON_STARTS`). The summary of `run.py` gives the matches per season and the lineups per kind.

## Rating baselines

```bash
uv run python -m edh.predictions.evaluate                               # every model, every identity
uv run python -m edh.predictions.evaluate --models elo glicko2 --identities players
```

| Option         | Default  | Effect                                                                   |
|----------------|----------|--------------------------------------------------------------------------|
| `--models`     | all      | `uniform`, `elo`, `glicko2`, `bradley_terry`                             |
| `--identities` | all      | `name`, `lineup`, `players`: who the ratings follow                      |
| `--workers`    | all CPUs | Worker processes, one model and identity each; 1 tunes in the main one   |

It reads the cached history only (run `python -m edh.predictions.run` first to update it; a history without played
matches in the tuning or evaluation season is an error). How models are tuned and scored:
[Walk-forward evaluation](/_docs/predictions.md#walk-forward-evaluation); the settings are in `constants.py`,
*Evaluation* and *Tuning* (`TUNING_SEASON`, `EVALUATION_SEASON`, the grids `ELO_GRID`, `GLICKO2_GRID`, `BT_GRID`, the
search's `*_BOUNDS`, `LOG_SCALE` and `TUNING_*`). Results don't depend on `--workers`. It logs a table, best first,
with the runs of each tuning and a star on the hyperparameters picked on a bound, then the share of series won by the
team listed first, and writes `_data/predictions/<last match day>/` (committed, to compare evaluations over time):

- `evaluation.json`: the seasons, `first_team_share`, and per model and identity the hyperparameters picked, those
  picked on a bound (`at_bound`), the tuning season's scores, the evaluation season's scores by group (`all`,
  `tier <n>`, `experienced`, `inexperienced`), and every run of the tuning in order (`trials`: `params`,
  `log_loss`). Scores: `log_loss` and `brier` (series winner), `accuracy`, `game_log_loss`, `matches`.
- `calibration.csv`: per model and identity, the evaluation season's series predictions by bin of predicted
  probability: `low`, `high`, `matches`, `predicted` (mean), `observed` (share won by the first team).

A saved report is explored in the [rating models notebook](/_notebooks/rating_models.py) (Marimo, Plotly, dark): a
reminder of each model, the comparison per group, the calibration from the favorite's side and the tuning's picks and
runs, each chart with its explanation. The project's Marimo settings are in `pyproject.toml` (`[tool.marimo]`: dark
theme by default).

```bash
uv sync --group notebooks                          # once: marimo and plotly (not installed by default)
uv run marimo run _notebooks/rating_models.py      # read-only, http://localhost:2718
uv run marimo edit _notebooks/rating_models.py     # to change it
```

From Python, `edh.predictions.series` turns a per-game probability into a series' score distribution
(`score_distribution(p, wins_needed, current)`) or win probability (`series_probability`), and the models of
`edh.predictions.ratings` can be used directly (`introduce`, `prepare`, `game_probability`, `update`).

## Forecasts

Forecasts predict matches not played yet, with the history before their day (how sides are resolved and why a
forecast equals the walk-forward's prediction: [Forecasts](/_docs/predictions.md#forecasts)). Their hyperparameters
are picked once, then every forecast reads them:

```bash
uv run python -m edh.predictions.predict --tune --when 2026-12-09        # pick the hyperparameters (a few minutes)
uv run python -m edh.predictions.predict "Team Vitality" "Karmine Corp" "Team Falcons" --when 2026-12-09
uv run python -m edh.predictions.predict "Team A" "Team B" --roster "Team B=Player1,Player2,Player3" --identities players
```

| Option         | Default    | Effect                                                                                  |
|----------------|------------|-----------------------------------------------------------------------------------------|
| `teams`        | none       | Teams as Liquipedia names them (two or more, or none with `--tune`); every pair         |
| `--tune`       | off        | Pick the hyperparameters first: the season before `--when`'s tunes, `--when`'s checks   |
| `--roster`     | none       | `TEAM=PLAYER,...` (player pages) for a team given: replaces its latest roster; repeatable|
| `--when`       | today      | Day of the matches (`YYYY-MM-DD`): the history before it is used                        |
| `--best-of`    | 5          | Series length quoted beside the per-game probability                                    |
| `--models`     | all        | `uniform`, `elo`, `glicko2`, `bradley_terry`                                            |
| `--identities` | all        | `name`, `lineup`, `players`                                                             |
| `--workers`    | all CPUs   | Tuning worker processes; 1 tunes in the main one                                        |

`--tune` logs the same table as the evaluation (a warning when `--when`'s season has no played match yet: the picks are
then checked on the tuning season itself) and writes `forecast_params.json` (same fields as `evaluation.json`) into
`_data/predictions/<last match day>/` (committed), keeping the models and identities already saved there on the same
seasons. Forecasts take each model and identity's latest picks and fail when one asked for was never tuned. A forecast
logs, per pair, one line per model and identity: the probability that the first team
wins a game, then the series, and the members never rated (a new team, a player without history: cold starts).

From Python:

```python
from edh.predictions.forecast import Forecaster
from edh.predictions.history import read_history
from edh.predictions.lineups import identify_lineups
from edh.predictions.ratings import Elo
import datetime as dt

matches = read_history()
model = Elo(k=18.24, shrink=0.2165, offset=-399.9)  # illustrative: take the picks from forecast_params.json
forecaster = Forecaster(matches, identify_lineups(matches), 'players', model, dt.datetime(2026, 12, 9, tzinfo=dt.UTC))
vitality, falcons = forecaster.side('Team Vitality'), forecaster.side('Team Falcons')
forecast = forecaster.forecast(vitality, falcons)  # game_probability, unseen, series_probability(wins_needed)
```

### Frozen forecasts

An event whose pairings aren't known in advance (a bracket) is forecast **once, before it starts**: every pair of its
teams, with every model and identity, saved to a file that is never overwritten and scored once the event is played.
`freeze.freeze(client, page, day, path)` reads the teams and rosters from the event's placements on Liquipedia (the
placements with a qualifier; every slot must be filled, or the error names the slots still `TBD` or without roster),
forecasts each pair for the event's first day with the latest picks of every model and identity (all must be tuned),
the rosters given replacing those of the history, and writes the file. It refuses once the event's first day has begun
(the cache may then hold its matches: only those before it are used), and warns about the teams never seen under their
name (cold starts for the name and lineup identities; check the names before committing). Each event gets a script in
[`_scripts/`](/_scripts/) holding its page, day and file:

```bash
uv run python -m edh.predictions.run                  # refresh the cache first
uv run python -m _scripts.freeze_kickoff_2027         # RLCS 2027 Kick-Off → _data/predictions/kickoff_2027/frozen.json
```

| Field             | Content                                                                                       |
|-------------------|-----------------------------------------------------------------------------------------------|
| `event`           | The event's page                                                                              |
| `forecast_day`    | The day forecast (the event's first), as `YYYY-MM-DD`                                         |
| `frozen_at`       | When the file was written (UTC, to the second)                                                |
| `history_through` | Day of the history's latest match                                                             |
| `picks`           | Each model and identity's hyperparameters                                                     |
| `teams`           | Each team (sorted): its roster (player pages) and, per identity, its members never rated      |
| `pairs`           | Each pair (sorted teams): per model then identity, the probability its first team wins a game |

Commit the file right away: the commit date shows it came before the event.

## Module map

| Module          | Role                                                                                  |
|-----------------|---------------------------------------------------------------------------------------|
| `constants.py`  | History scope, queried fields, retries, cache files, lineup rules, tuning settings    |
| `models.py`     | `Player`, `Opponent`, `PlayedMatch`, `Lineup`, `Lineups` (frozen, validated)          |
| `history.py`    | `HistoryScope`, `update_history` (fetch and cache), `read_history`, `to_played_match` |
| `lineups.py`    | `identify_lineups`, `side_kind`, `season_of`                                          |
| `series.py`     | Per-game probability → series score distribution and win probability                  |
| `ratings.py`    | `Uniform`, `Elo`, `Glicko2`, `BradleyTerry` (the `RatingModel` protocol)              |
| `evaluation.py` | `walk_forward` (`Replay`), `score`, `calibration` (`favorite_calibration`), `breakdown` |
| `tuning.py`     | `MODELS`, `Seasons`, `tune` (grid, then bounded search), `evaluate_all`, seasons      |
| `evaluate.py`   | Evaluation command line entry point and report                                        |
| `forecast.py`   | `Forecaster` (replay, then any pair), `Side`, `Forecast`                              |
| `predict.py`    | Forecasts command line entry point (`--tune`, then forecasts of given teams)          |
| `freeze.py`     | `freeze` (every pair of an event's teams, saved once), `event_teams`                  |
| `run.py`        | History command line entry point                                                      |

Liquipedia access shared with the other projects (API key, client, match parsing) lives in
[`edh/lpdb.py`](/edh/lpdb.py).
