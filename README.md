# `esports-data-hub`

**A repository containing projects related to esports and data.**

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json&style=flat-square)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/github/license/Dyl-M/esports-data-hub?style=flat-square)](LICENSE)

![Status](https://img.shields.io/badge/status-in%20development-orange?style=flat-square)
[![Test & Coverage](https://img.shields.io/github/actions/workflow/status/Dyl-M/esports-data-hub/test-coverage.yml?branch=dev&label=Test%20%26%20Coverage&style=flat-square&logo=github-actions&logoColor=white)](https://github.com/Dyl-M/esports-data-hub/actions/workflows/test-coverage.yml)
[![DeepSource](https://app.deepsource.com/gh/Dyl-M/esports-data-hub.svg/?label=active+issues&show_trend=true&token=TOKEN)](https://app.deepsource.com/gh/Dyl-M/esports-data-hub/)
[![DeepSource](https://app.deepsource.com/gh/Dyl-M/esports-data-hub.svg/?label=code+coverage&show_trend=true&token=TOKEN)](https://app.deepsource.com/gh/Dyl-M/esports-data-hub/)

## League play scenarios

The first project computes ranking and qualification scenarios for teams during the league play stage of an esports
event: given the results already played, it works out how each team can finish once the remaining matches are played,
either by enumerating every possible outcome or by Monte Carlo sampling. Match data comes from
[Liquipedia](https://liquipedia.net/) through [`liquipydia`](https://github.com/Dyl-M/liquipydia).

Primary targets (Rocket League):

- Coupe de France Slash 2026
- The upcoming RLCS season, including the Clubs Championships league play

It succeeds [`league-play-scenarios`](https://github.com/Dyl-M/league-play-scenarios).

### How it works

- **Format**: each league has a `_data/<league>/league.json` (series length, ordered tiebreakers, qualification
  spots, Liquipedia page), written from its Liquipedia page.
- **Scenarios**: every reachable final score of a remaining series is equally likely (a Bo5 at 0-0 ends 3-0, 3-1,
  3-2, 0-3, 1-3 or 2-3); a series in progress continues from its current score. Up to 10^6 scenarios are enumerated
  exactly, larger counts are sampled (Monte Carlo).
- **Tiebreakers** follow Liquipedia's standings rules, including mini-league (`ml.`) tiebreakers computed on the
  series between the tied teams only. Goal difference is unknown for series not played yet, so a tie only goals can
  settle is reported as such instead of guessed.

### Usage

The Liquipedia source needs an [LPDB API key](https://liquipedia.net/api), read from the `LPDB_API_KEY` environment
variable or from the `lpdb` field of `.tokens/local_keys.json` (gitignored).

```bash
uv run python -m edh.scenarios.run cdf_slash_2026                    # fetch from Liquipedia, then evaluate
uv run python -m edh.scenarios.run cdf_slash_2026 --source csv       # offline, from the saved results.csv
uv run python -m edh.scenarios.run cdf_slash_2026 --method montecarlo --samples 200000 --seed 1
```

Each run prints, per team, the share of scenarios ending at each rank and qualifying, and saves it to
`_data/<league>/outlook/` (`outlook.csv` / `outlook.json`, gitignored). A Liquipedia run also saves the results to
`_data/<league>/results.csv`.

Teams are listed in **projected standings** order: each rank goes to one team, taking team / rank pairings from the
most to the least likely (a team losing its most likely rank to a likelier team falls back to its next most likely free
rank).

## Repository structure

```
esports-data-hub/
├── .github          # GitHub Actions workflows (tests & coverage, license year), Dependabot config, CODEOWNERS
├── _data            # One folder per league: league.json (format) and results.csv (results snapshot)
├── _tests           # Pytest test suite, CSV and recorded Liquipedia fixtures, shared configuration
└── edh              # Main application package: exceptions, paths
    └── scenarios    # League play scenarios: models, config, standings / tiebreakers, engine, report, run.py (CLI)
        └── sources  # Result sources: Liquipedia (through liquipydia) and CSV
```

## Development

The project uses [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync                              # install dependencies (dev tools included)
uv run pytest -m "not integration"   # tests (the integration ones call the live Liquipedia API)
uv run ruff check .                  # lint
uv run ruff format --check .         # format check
uv run mypy edh                      # type check
```

Code is reviewed by the [DeepSource](https://deepsource.io/) bot.
