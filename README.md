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
either exactly (enumerating every possible outcome, or every series winner first) or by Monte Carlo sampling. Match
data comes from
[Liquipedia](https://liquipedia.net/) through [`liquipydia`](https://github.com/Dyl-M/liquipydia).

Primary targets (Rocket League):

- Coupe de France Slash 2026
- The upcoming RLCS season, including the Clubs Championships league play

It succeeds [`league-play-scenarios`](https://github.com/Dyl-M/league-play-scenarios).

### How it works

- **Format**: each league has a `_data/<league>/league.json` (series length, ordered tiebreakers, qualification
  spots, Liquipedia page), written from its Liquipedia page, plus optional `theme` colors for its outlook card.
- **Scenarios**: every reachable final score of a remaining series is equally likely (a Bo5 at 0-0 ends 3-0, 3-1,
  3-2, 0-3, 1-3 or 2-3); a series in progress continues from its current score. Up to 10^6 scenarios are enumerated
  exactly. Beyond that, when the first tiebreaker is series wins, the **winners-first** method still gives exact
  shares: it enumerates who wins each series (2 options instead of 6) and only looks at game margins for teams tied
  on wins (up to 2^15 winner combinations, i.e. 15 series left; a run can take minutes). Larger counts are sampled (Monte Carlo).
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
uv run python -m edh.scenarios.run cdf_slash_2026 --workers 4        # cap worker processes (default: all CPUs)
uv run python -m edh.scenarios.run cdf_slash_2026 --visual           # evaluate, then render the outlook card
uv run python -m edh.scenarios.run cdf_slash_2026 --visual-only      # render the card from the saved outlook
```

Each run prints, per team, the share of scenarios ending at each rank and qualifying, and saves it to
`_data/<league>/outlook/` (`outlook.csv` / `outlook.json`, gitignored). A Liquipedia run also saves the results to
`_data/<league>/results.csv` and the event organizers to `_data/<league>/event.json`.

Teams are listed in **projected standings** order: each rank goes to one team, taking team / rank pairings from the
most to the least likely (a team losing its most likely rank to a likelier team falls back to its next most likely free
rank).

### Outlook card

`--visual` turns the outlook into a card to share on social media, saved as `card_portrait.png` (1080×1350),
`card_square.png` (1080×1080) and `card_landscape.png` (1200×675) in `_data/<league>/outlook/`. Each team row shows its
share of scenarios reaching the league's first qualification slot and its share of each final rank. The footer names
the evaluation method and credits the event organizers for the results (read from Liquipedia). The card is an HTML
template rendered by a headless Chromium through [Playwright](https://playwright.dev/python/), installed once with:

```bash
uv run playwright install chromium
```

The card template (`edh/scenarios/visuals/templates/`) and the brand files it embeds (`edh/assets/`: profile picture,
font, social icons) are kept out of git, so cards can only be rendered where those files exist locally; without them,
`--visual` stops with an error naming the missing file.

## Repository structure

```
esports-data-hub/
├── .github          # GitHub Actions workflows (tests & coverage, license year), Dependabot config, CODEOWNERS
├── _data            # One folder per league: league.json (format), results.csv and event.json (Liquipedia snapshots)
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
