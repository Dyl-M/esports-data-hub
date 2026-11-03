# esports-data-hub

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
