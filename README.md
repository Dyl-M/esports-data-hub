# esports-data-hub

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json&style=flat-square)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/github/license/Dyl-M/esports-data-hub?style=flat-square)](LICENSE)

![Status](https://img.shields.io/badge/status-in%20development-orange?style=flat-square)
[![Test & Coverage](https://img.shields.io/github/actions/workflow/status/Dyl-M/esports-data-hub/test-coverage.yml?branch=dev&label=Test%20%26%20Coverage&style=flat-square&logo=github-actions&logoColor=white)](https://github.com/Dyl-M/esports-data-hub/actions/workflows/test-coverage.yml)
[![DeepSource](https://app.deepsource.com/gh/Dyl-M/esports-data-hub.svg/?label=active+issues&show_trend=true&token=TOKEN)](https://app.deepsource.com/gh/Dyl-M/esports-data-hub/)
[![DeepSource](https://app.deepsource.com/gh/Dyl-M/esports-data-hub.svg/?label=code+coverage&show_trend=true&token=TOKEN)](https://app.deepsource.com/gh/Dyl-M/esports-data-hub/)

A repository containing projects related to esports and data.

## League play scenarios

The first project computes ranking and qualification scenarios for teams during the league play stage of an esports
event: given the results already played, it works out how each team can finish once the remaining matches are played,
either by enumerating every possible outcome or by Monte Carlo sampling. Match data comes from
[Liquipedia](https://liquipedia.net/) through [`liquipydia`](https://github.com/Dyl-M/liquipydia).

Primary targets (Rocket League):

- Coupe de France Slash 2026
- The upcoming RLCS season, including the Clubs Championships league play

It succeeds [`league-play-scenarios`](https://github.com/Dyl-M/league-play-scenarios). This project is a work in
progress.

## Development

The project uses [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync                          # install dependencies (dev tools included)
uv run pytest                    # tests
uv run ruff check .              # lint
uv run ruff format --check .     # format check
uv run mypy edh                  # type check
```

Code is reviewed by the [DeepSource](https://deepsource.io/) bot.
