![Esports Data Hub: a collection of projects around data through esports competitions](.github/assets/banner.png)

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json&style=flat-square)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/github/license/Dyl-M/esports-data-hub?style=flat-square)](LICENSE)

![Status](https://img.shields.io/badge/status-in%20development-orange?style=flat-square)
[![Test & Coverage](https://img.shields.io/github/actions/workflow/status/Dyl-M/esports-data-hub/test-coverage.yml?branch=main&label=Test%20%26%20Coverage&style=flat-square&logo=github-actions&logoColor=white)](https://github.com/Dyl-M/esports-data-hub/actions/workflows/test-coverage.yml)
[![DeepSource](https://app.deepsource.com/gh/Dyl-M/esports-data-hub.svg/?label=active+issues&show_trend=true&token=TOKEN)](https://app.deepsource.com/gh/Dyl-M/esports-data-hub/)
[![DeepSource](https://app.deepsource.com/gh/Dyl-M/esports-data-hub.svg/?label=code+coverage&show_trend=true&token=TOKEN)](https://app.deepsource.com/gh/Dyl-M/esports-data-hub/)

## Projects

**Esports Data Hub** (EDH) gathers several data projects built on esports competition results, each in its own
subpackage of `edh`, sharing the same tooling and data sources.

| Project                                         | Package         | Status            |
|-------------------------------------------------|-----------------|-------------------|
| [League play scenarios](#league-play-scenarios) | `edh.scenarios` | First version out |

League play scenarios is the first of them; more projects will follow.

## League play scenarios

This project computes ranking and qualification scenarios for teams during the league play stage of an esports
event: given the results already played, it works out how each team can finish once the remaining matches are played,
either exactly or by Monte Carlo sampling. Match data comes from [Liquipedia](https://liquipedia.net/) through
[`liquipydia`](https://github.com/Dyl-M/liquipydia).

Primary targets (Rocket League):

- Coupe de France Slash 2026
- The upcoming RLCS season, including the Clubs Championships league play

It succeeds [`league-play-scenarios`](https://github.com/Dyl-M/league-play-scenarios).

The probability model, the tiebreakers, the methods, the outlook card and their limits are explained in
**[How league play scenarios work](_docs/scenarios.md)**. In short, every possible score of a remaining series counts
as equally likely, so the shares describe what is still open, not predictions.

### Usage

The Liquipedia source needs an [LPDB API key](https://liquipedia.net/api), read from the `LPDB_API_KEY` environment
variable or from the `lpdb` field of `.tokens/local_keys.json` (gitignored).

```bash
uv run python -m edh.scenarios.run cdf_slash_2026                    # fetch from Liquipedia, then evaluate
uv run python -m edh.scenarios.run cdf_slash_2026 --source csv       # offline, from the saved results.csv
uv run python -m edh.scenarios.run cdf_slash_2026 --method montecarlo --samples 200000 --seed 1
uv run python -m edh.scenarios.run cdf_slash_2026 --workers 4        # cap worker processes (default: all CPUs)
uv run python -m edh.scenarios.run cdf_slash_2026 --visual           # evaluate, then render the outlook card
uv run python -m edh.scenarios.run cdf_slash_2026 --visual-only      # render the card from the saved outlook
```

Each run prints, per team, the share of scenarios ending at each rank and qualifying, and saves it to
`_data/<league>/outlook/` (`outlook.csv` / `outlook.json`, gitignored). A Liquipedia run also saves the results to
`_data/<league>/results.csv` and the event organizers to `_data/<league>/event.json`, and downloads the competition
and team logos to `_data/<league>/logos/` (gitignored, kept local).

`--visual` also renders the outlook as a shareable [card](_docs/scenarios.md#outlook-card) (PNG, three social media
formats). It needs Playwright's Chromium, installed once with `uv run playwright install chromium`, and the card
template and brand files, which are kept out of git.

## Repository structure

```
esports-data-hub/
├── .github          # GitHub Actions workflows (tests & coverage, license year), Dependabot config, CODEOWNERS
├── _data            # One folder per league: league.json (format), results.csv and event.json (Liquipedia snapshots)
├── _docs            # Project documentation (how league play scenarios work)
├── _tests           # Pytest test suite, CSV / recorded Liquipedia / placeholder visual fixtures, shared configuration
└── edh              # Main application package: exceptions, paths, brand (one subpackage per project)
    └── scenarios    # League play scenarios: models, config, standings / tiebreakers, engine, report, run.py (CLI)
        ├── sources  # Result sources: Liquipedia (through liquipydia) and CSV, event details snapshot
        └── visuals  # Outlook card: HTML rendering (Jinja2) and PNG export (Playwright)
```

## Development

The project uses [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync                                              # install dependencies (dev tools included)
uv run pytest -m "not integration and not browser"   # tests run by CI (see below)
uv run ruff check .                                  # lint
uv run ruff format --check .                         # format check
uv run mypy edh                                      # type check
```

Two opt-in test markers stay out of CI: `integration` calls the live Liquipedia API (needs an LPDB API key), `browser`
renders cards with Playwright's Chromium.

Code is reviewed by the [DeepSource](https://deepsource.io/) bot.
