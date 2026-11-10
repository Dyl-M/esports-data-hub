# Rating predictions (`edh.predictions`)

Per-game win probabilities learned from past results, meant to replace the "every score is equally likely" model of
the [league play scenarios](/edh/scenarios/README.md). So far it holds the first building block: the **match
history**, fetched from Liquipedia and cached locally. Ratings and their evaluation come next.

## Contents

- [Setup](#setup)
- [Match history](#match-history)
- [Running](#running)
- [Reading the history](#reading-the-history)
- [Module map](#module-map)

## Setup

The [shared setup](/README.md#setup), including an LPDB API key. Nothing else: the history needs no extra dependency.

## Match history

| Scope  | Value                                                                                              |
|--------|----------------------------------------------------------------------------------------------------|
| Game   | Rocket League (Liquipedia wiki `rocketleague`)                                                     |
| Period | From September 1, 2024 (`HISTORY_START`): RLCS 2025 to warm ratings up, RLCS 2026 to evaluate them |
| Tiers  | Liquipedia tiers 1 and 2 (`HISTORY_TIERS`)                                                         |
| Mode   | 3v3 when read (`TEAM_MODE`); 1v1 / 2v2 events are cached but left out                              |

**Cache.** Matches are stored in `.cache/lpdb/<wiki>/` (gitignored, about 3 KB per match, some 64 MB for the scope):

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

The summary gives the finished 3v3 matches, the teams and the matches per year.

## Reading the history

```python
from edh.predictions.history import read_history

matches = read_history()  # finished 3v3 matches of the default scope, oldest first
```

Each `PlayedMatch` has its LPDB id and page, its start time in UTC, the tier, whether it was played on LAN (None when
unknown), the two `Opponent`s (team name, team template of the period, series score, roster as `Player`s whose
Liquipedia page is their identity across teams) and the goals of each decided game. Walkovers are kept, flagged,
scored 1-0 and without games. Matches not finished, without two named teams, or without a winner are left out.

Team names are used as Liquipedia gives them: a renamed team appears under each of its names. Ratings will follow
lineups (a core of players kept together) rather than names.

## Module map

| Module         | Role                                                                                  |
|----------------|---------------------------------------------------------------------------------------|
| `constants.py` | History scope, queried fields, retry settings, cache file names                       |
| `models.py`    | `Player`, `Opponent`, `PlayedMatch` (frozen, validated)                               |
| `history.py`   | `HistoryScope`, `update_history` (fetch and cache), `read_history`, `to_played_match` |
| `run.py`       | Command line entry point                                                              |

Liquipedia access shared with the other projects (API key, client, match parsing) lives in
[`edh/lpdb.py`](/edh/lpdb.py).
