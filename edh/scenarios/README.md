# League play scenarios (`edh.scenarios`)

Ranking and qualification scenarios for the teams of a round-robin league: from the results already played, every way
the remaining series can end is ranked with the league's tiebreakers, exactly or by Monte Carlo sampling, and turned
into per-team shares and a shareable outlook card.

This page is about running it. What the shares mean, the probability model, the tiebreaker rules and the math of the
three methods are in **[How league play scenarios work](/_docs/scenarios.md)**.

## Contents

- [Setup](#setup)
- [Leagues](#leagues)
- [Running](#running)
- [Choosing a method](#choosing-a-method)
- [Outputs](#outputs)
- [Outlook card](#outlook-card)
- [Module map](#module-map)

## Setup

On top of the [shared setup](/README.md#setup):

- **Liquipedia source** (the default): an LPDB API key, as described in the shared setup.
- **Cards** (`--visual`, `--visual-only`): Playwright's Chromium, installed once with
  `uv run playwright install chromium`, and the brand files in `edh/assets/` (profile picture, font, social icons),
  which are kept out of git. Without them, rendering stops with an error naming the missing file.

## Leagues

A league is a folder of `_data/`:

| File                      | In git | Written by            | Content                                                  |
|---------------------------|--------|-----------------------|----------------------------------------------------------|
| `league.json`             | yes    | hand, from Liquipedia | The league's format (below)                              |
| `results.csv`             | yes    | each Liquipedia run   | Results snapshot, the input of `--source csv`            |
| `event.json`              | yes    | each Liquipedia run   | Event details shown on the card (organizers)             |
| `outlook/<day>/<method>/` | no     | each run              | Computed shares and cards (see [Outputs](#outputs))      |
| `logos/`                  | no     | each Liquipedia run   | Competition and team logos, with a `logos.json` manifest |

`league.json` fields (take them from the event's Liquipedia page and rulebook, not from memory):

| Field                | Required | Meaning                                                                                   |
|----------------------|----------|-------------------------------------------------------------------------------------------|
| `name`               | yes      | Name shown on the card                                                                    |
| `wiki`, `pagename`   | yes      | Liquipedia wiki and page, e.g. `rocketleague`, `NEO/Coupe_de_France_Slash/2026`           |
| `best_of`            | yes      | Series length (odd); a series of $2k-1$ games is won at $k$ games                         |
| `tiebreakers`        | yes      | Ordered Liquipedia tiebreaker ids, e.g. `["matchscore", "gamediff", "ml.gamediff"]`       |
| `qualification`      | no       | Slots as `label: [first rank, last rank]`, e.g. `"Playoffs": [1, 4]`                      |
| `team_aliases`       | no       | Old name → current name, applied to every series (and manual points) before anything else |
| `manual_tiebreakers` | no       | Points set by hand, read by the `manual` tiebreaker (0 when absent), e.g. RLCS seed order |
| `walkover_games`     | no       | Games credited to the winner of a walkover (0 by default)                                 |
| `theme`              | no       | Card colors, see [Outlook card](#outlook-card)                                            |

The tiebreaker ids and how they apply are listed in
[Standings and tiebreakers](/_docs/scenarios.md#standings-and-tiebreakers); the card shows the first qualification
slot.

`results.csv` columns: `round, team_1, team_2, score_1, score_2, finished, goals, date`. `finished` is `1` or `0`,
`goals` lists each game's goals (`7-2;2-0;4-1`, empty when unknown), `date` is the series' day in the event's
timezone (optional, older snapshots don't have it). It can be edited by hand to try a result, then evaluated with
`--source csv`.

## Running

```bash
uv run python -m edh.scenarios.run cdf_slash_2026                    # fetch from Liquipedia, then evaluate
uv run python -m edh.scenarios.run cdf_slash_2026 --source csv       # offline, from the saved results.csv
uv run python -m edh.scenarios.run cdf_slash_2026 --method montecarlo --samples 200000 --seed 1
uv run python -m edh.scenarios.run cdf_slash_2026 --workers 4        # cap worker processes (default: all CPUs)
uv run python -m edh.scenarios.run cdf_slash_2026 --visual           # evaluate, then render the outlook card
uv run python -m edh.scenarios.run cdf_slash_2026 --visual-only      # render the card from the latest saved outlook
uv run python -m edh.scenarios.run 'rlcs_2027_*' --visual           # several leagues: names and / or patterns
```

| Option          | Default      | Effect                                                                                      |
|-----------------|--------------|---------------------------------------------------------------------------------------------|
| `<league> ...`  |              | `_data/` folder names, or patterns (`*`, `?`, `[`) over the folders holding a `league.json` |
| `--source`      | `liquipedia` | `liquipedia` (live, then snapshots saved) or `csv` (the saved `results.csv`)                |
| `--method`      | `auto`       | `auto`, `exhaustive`, `winnersfirst` or `montecarlo` (see below)                            |
| `--samples`     | 100,000      | Monte Carlo samples                                                                         |
| `--seed`        | none         | Monte Carlo seed: the same seed gives the same result, whatever `--workers` is              |
| `--workers`     | all CPUs     | Worker processes; small runs stay in the main process                                       |
| `--visual`      | off          | Also render the outlook card                                                                |
| `--visual-only` | off          | Only render the card of the latest saved outlook (of `--method`, unless `auto`)             |

Several leagues run one after the other with the same options. A league that fails is logged with its name, the others
still run, and the command exits with code 1 if any failed.

Scripts calling `evaluate` directly need an `if __name__ == '__main__':` guard: worker processes are started with
Windows' spawn method, which imports the main module again.

## Choosing a method

`--method auto` takes the first method that fits, in this order:

| Method                                 | Exact | Applies when                                        | `auto` limit                                                 |
|----------------------------------------|-------|-----------------------------------------------------|--------------------------------------------------------------|
| `exhaustive`                           | yes   | always                                              | $\le 10^6$ scenarios (`MAX_EXHAUSTIVE_SCENARIOS`)            |
| `winnersfirst` (shown Semi-exhaustive) | yes   | the first tiebreaker depends on series winners only | $\le 2^{15}$ winner combinations (`MAX_WINNER_COMBINATIONS`) |
| `montecarlo`                           | no    | always                                              | fallback                                                     |

An explicit exact method above its limit, or `winnersfirst` on a league whose first tiebreaker reads game or goal
margins, stops with an error. Roughly, exhaustive covers the last 7 Bo5 series of a league, winners-first the last 15;
how each method works and why the exact ones agree is explained in
[How league play scenarios work](/_docs/scenarios.md).

## Outputs

Each run prints, per team, the share of scenarios ending at each rank and qualifying, in projected standings order,
and saves the outlook to `_data/<league>/outlook/<YYYY-MM-DD>/<method>/`:

- **The day** is the league's progress, not the day of the run: the last day a series finished, in the event's
  timezone (also the date on the card). With no finished series dated, today is used, with a warning.
- **The method** is the one actually used: an `auto` run lands under `exhaustive`, `winnersfirst` or `montecarlo`. A
  rerun replaces only its own day and method, so days and methods can be compared.
- `outlook.json`: the whole report (league, method, scenarios or samples, remaining series, per team: rank shares,
  qualified / undecided / slot shares per slot, unresolved share, expected and projected rank). `--visual-only`
  renders from it.
- `outlook.csv`: one row per team: `team`, `expected_rank`, `projected_rank`, `rank_1` … `rank_N`, then per slot
  `<slot>_share`, `<slot>_qualified`, `<slot>_undecided`, and `unresolved`.

With the Liquipedia source, a run also rewrites `results.csv` and `event.json` and refreshes `logos/` (an address
already downloaded is not fetched again).

## Outlook card

`--visual` renders the outlook as `card_portrait.png` (1080×1350), `card_square.png` (1080×1080) and
`card_landscape.png` (1200×675), next to the outlook.

- One row per team, in projected standings order: its **slot share** of the league's **first** qualification slot
  (the sum of the slot's rank cells, up to rounding), with the share of scenarios where a tiebreak still settles the
  spot under it ("0.3% on tiebreak"), a qualified / tiebreak bar, and a strip of its share of each final rank (a gap
  after each slot cutoff, the projected rank outlined). IN / OUT badges appear on exact runs only. Shares have one
  decimal; `-` and `100%` are kept for exact 0 and 1, so a share that would round to them is written `<0.1` / `>99.9`.
- The competition logo sits in the top right corner; each team's logo is a faint, slightly tilted watermark behind
  its name. Logos are cropped to what they actually draw and sized to cover the same area, so a wide logo doesn't look
  smaller than a square one; teams without branding of their own get Liquipedia's default logo of the game.
- Leagues with more than 8 ranks (the 12-team RLCS regions) get a denser layout: smaller text, a wider rank strip and
  smaller watermarks. Smaller leagues keep the regular layout.
- The footer names the evaluation method and the source of the results: the event's organizers (read from Liquipedia,
  without the disambiguation of their page names, e.g. `BLAST (Organizer)` is shown as `BLAST`).

Colors come from the league's `theme`, picked by hand from the event's artwork; any color left out keeps the neutral
palette. Qualified / eliminated / tiebreak colors are fixed. Each color is `#rrggbb`, or `#rrggbbaa` when translucent:

| Key          | Used for                               |
|--------------|----------------------------------------|
| `background` | Card background                        |
| `surface`    | Empty rank cells and bar tracks        |
| `line`       | Row separators                         |
| `text`       | Main text                              |
| `muted`      | Labels and secondary text              |
| `heat`       | Rank strip color (the share of a rank) |
| `accent`     | Title highlight                        |
| `glow`       | Radial glow in the top right corner    |

The card is a Jinja2 HTML template rendered by a headless Chromium through [Playwright](https://playwright.dev/python/).
The template ([`visuals/templates/card.html.j2`](/edh/scenarios/visuals/templates/card.html.j2)) is public, so
anyone can check how the card displays the results.

## Module map

| Module         | Role                                                                                                                    |
|----------------|-------------------------------------------------------------------------------------------------------------------------|
| `config.py`    | `league.json` → `LeagueConfig`; paths under `_data/<league>/` (`league_path`)                                           |
| `constants.py` | Methods, limits, tiebreaker ids, file names, card formats                                                               |
| `models.py`    | Frozen, validated dataclasses (`Series`, `Tiebreaker`, `RankGroup`, `TeamOutlook`, …)                                   |
| `outcomes.py`  | Reachable final scores of a series                                                                                      |
| `standings.py` | `tally`, `rank`, `Standings` (played series tallied once), `TieResolver`                                                |
| `engine.py`    | `evaluate`: method choice, chunks, exhaustive / Monte Carlo, aggregation, projection                                    |
| `winners.py`   | Winners-first exact method                                                                                              |
| `report.py`    | Console table, `outlook.csv` / `outlook.json`                                                                           |
| `sources/`     | Liquipedia (through [`liquipydia`](https://github.com/Dyl-M/liquipydia)) and CSV result sources, event details snapshot |
| `logos.py`     | Logo downloads and trimming                                                                                             |
| `visuals/`     | Card context / HTML rendering (`card.py`) and PNG export (`export.py`)                                                  |
| `run.py`       | Command line entry point                                                                                                |

Liquipedia access shared with the other projects (API key, client, match parsing) lives in
[`edh/lpdb.py`](/edh/lpdb.py).
