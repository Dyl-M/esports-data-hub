"""Tests for edh/predictions/predict.py - forecasts command line and tuning."""

# Standard library
import datetime as dt
import json
import logging

# Third-party
import pytest

# Local
from edh import paths
from edh.predictions import predict
from edh.predictions.forecast import Forecast, Side

from .conftest import help_opens_with_docstring, make_match, make_opponent, parse_error, run_as_main, use_history

START = dt.datetime(2026, 6, 1, 18, tzinfo=dt.UTC)  # RLCS 2026 until September 20, then RLCS 2027
FAST = ['--models', 'uniform', 'elo', '--identities', 'name', 'players', '--workers', '1']
TUNE = ['--tune', '--when', '2027-01-08', *FAST]  # tuned on RLCS 2026, checked on RLCS 2027


def _history(weeks=30):
    """RLCS 2026 and 2027 (from week 16): Team A beats Team B 3-1 every week."""
    return [
        make_match(
            make_opponent('Team A', 3, 'a1 a2 a3'),
            make_opponent('Team B', 1, 'b1 b2 b3'),
            match_id=f'm{week:02d}',
            played_at=START + dt.timedelta(weeks=week),
        )
        for week in range(weeks)
    ]


LAST_DAY = (START + dt.timedelta(weeks=29)).date().isoformat()


@pytest.fixture
def history(monkeypatch, tmp_path):
    """The small history as the cache, `_data/` in a temporary folder."""
    return use_history(monkeypatch, tmp_path, predict, _history)


def _path(folder=LAST_DAY):
    """The forecasts' tuning file in `_data/predictions/<folder>/`."""
    return paths.DATA_DIR / 'predictions' / folder / 'forecast_params.json'


def _saved(folder=LAST_DAY):
    """The forecasts' tuning saved in `_data/predictions/<folder>/`."""
    return json.loads(_path(folder).read_text(encoding='utf-8'))


def _tasks(content):
    """The model and identity of each result of a saved tuning."""
    return [(row['model'], row['identity']) for row in content['results']]


def _save(folder, seasons, *tasks):
    """Save a forecasts' tuning on these seasons with these (model, identity, k) picks."""
    results = [{'model': model, 'identity': identity, 'params': {'k': k}} for model, identity, k in tasks]
    content = {'tuning_season': seasons[0], 'evaluation_season': seasons[1], 'results': results}
    predict.write_json(content, _path(folder))


# === Tuning ===


def test_tune_saves_the_picks(history, caplog):
    """Test that `--tune` picks on RLCS 2026, scores RLCS 2027 and saves the report beside the evaluation's."""
    with caplog.at_level(logging.INFO):
        assert predict.main(TUNE) == 0
    saved = _saved()
    assert (saved['tuning_season'], saved['evaluation_season']) == (2026, 2027)
    assert _tasks(saved) == [
        ('uniform', 'name'),
        ('uniform', 'players'),
        ('elo', 'name'),
        ('elo', 'players'),
    ]
    assert 'RLCS 2027 (tuned on RLCS 2026)' in caplog.text
    assert 'forecast_params.json' in caplog.text
    assert ' vs ' not in caplog.text  # no team, no forecast


def test_tune_before_the_season_under_way(monkeypatch, history, caplog):
    """Test that without a played match of the forecast's season, the picks are checked on the tuning season."""
    monkeypatch.setattr(predict, 'read_history', lambda: _history(16))  # RLCS 2026 only
    assert predict.main(['--tune', '--when', '2026-12-01', *FAST]) == 0
    saved = _saved((START + dt.timedelta(weeks=15)).date().isoformat())
    assert (saved['tuning_season'], saved['evaluation_season']) == (2026, 2026)
    assert 'No played match of RLCS 2027 yet: the picks are checked on RLCS 2026' in caplog.text


def test_tune_keeps_the_other_picks_of_the_same_seasons(history):
    """Test that a tuning of some models keeps the others saved that day on the same seasons, replacing its own."""
    _save(LAST_DAY, (2026, 2027), ('glicko2', 'name', 1), ('elo', 'name', 2))
    assert predict.main([*TUNE, '--models', 'elo', '--identities', 'name']) == 0
    saved = _saved()
    assert _tasks(saved) == [('glicko2', 'name'), ('elo', 'name')]
    assert saved['results'][1]['params']['k'] != 2
    assert saved['first_team_share'] == 1.0  # the new tuning's report


def test_tune_drops_picks_of_other_seasons(history):
    """Test that picks saved that day on other seasons are replaced, not merged."""
    _save(LAST_DAY, (2025, 2026), ('glicko2', 'name', 1))
    assert predict.main([*TUNE, '--models', 'elo', '--identities', 'name']) == 0
    assert _tasks(_saved()) == [('elo', 'name')]


def test_latest_picks_per_model_and_identity(history):
    """Test that each model and identity takes its picks from the latest tuning holding it."""
    _save('2026-11-01', (2026, 2027), ('elo', 'name', 10), ('glicko2', 'name', 1))
    _save('2026-11-09', (2026, 2027), ('elo', 'name', 20))
    (history / 'predictions' / '2026-12-01').mkdir()  # an evaluation only
    assert predict.latest_picks() == {('elo', 'name'): {'k': 20}, ('glicko2', 'name'): {'k': 1}}


# === Forecasts ===


def test_forecasts_every_pair(history, caplog):
    """Test that every pair is forecast with each model and identity, cold starts named."""
    assert predict.main(TUNE) == 0
    with caplog.at_level(logging.INFO):
        assert predict.main(['Team A', 'Team B', 'Team C', '--when', '2027-01-08', *FAST]) == 0
    assert f'Forecasts for 2027-01-08, history through {LAST_DAY}' in caplog.text
    assert 'Team A vs Team B: model / identity / game / best of 5' in caplog.text
    assert 'Team A vs Team C' in caplog.text
    assert 'Team B vs Team C' in caplog.text
    assert caplog.text.count('never rated: Team C (new team)') == 8  # two pairs, four models and identities
    assert '  uniform        name     0.500 0.500\n' in caplog.text


def test_given_roster_and_series_length(history, caplog):
    """Test that a given roster is rated by the players identity, and the series length asked for is quoted."""
    assert predict.main(TUNE) == 0
    with caplog.at_level(logging.INFO):
        argv = ['Team A', 'Team B', '--roster', 'Team B=b1, b2 ,x9', '--best-of', '7', *FAST]
        assert predict.main(argv) == 0
    assert 'best of 7' in caplog.text
    assert caplog.text.count('never rated: Team B: x9') == 2  # the players identity of both models


@pytest.mark.parametrize(('best_of', 'series'), [(1, '0.600'), (5, '0.683'), (7, '0.710')])
def test_forecast_lines_quote_the_series(best_of, series):
    """Test that each line gives the game probability, then the series one for the length asked for."""
    side = Side('Team A', None, frozenset())
    forecast = Forecast(side, Side('Team B', 0, frozenset()), 0.6, ((), ()))
    lines = predict.forecast_lines([('Team A', 'Team B', {('elo', 'name'): forecast})], best_of)
    assert lines == [
        f'Team A vs Team B: model / identity / game / best of {best_of}',
        f'  elo            name     0.600 {series}',
    ]


def test_forecasts_without_tuning(history, caplog):
    """Test that forecasting before any tuning is an error pointing to `--tune`."""
    assert predict.main(['Team A', 'Team B']) == 1
    assert 'predict --tune' in caplog.text


def test_forecasts_need_every_pick(history, caplog):
    """Test that a model or identity missing from the latest tuning is an error naming it."""
    assert predict.main([*TUNE, '--models', 'uniform', '--identities', 'name']) == 0
    assert predict.main(['Team A', 'Team B', '--models', 'uniform', 'elo', '--identities', 'name', 'lineup']) == 1
    assert 'No tuned hyperparameters for uniform / lineup, elo / name, elo / lineup: run with --tune' in caplog.text


def test_default_day_is_today(history, monkeypatch, caplog):
    """Test that forecasts are for today by default."""
    assert predict.main(TUNE) == 0
    today = dt.datetime.now(dt.UTC).date()
    with caplog.at_level(logging.INFO):
        assert predict.main(['Team A', 'Team B', *FAST]) == 0
    assert f'Forecasts for {today}' in caplog.text


# === Command line ===


@pytest.mark.parametrize('argv', [[], ['Team A'], ['Team A', '--tune']])
def test_teams_or_tune_needed(argv, capsys):
    """Test that a run needs two teams or more, or none with `--tune`."""
    assert 'give at least two teams (or none with --tune)' in parse_error(predict, argv, capsys)


def test_roster_of_a_team_not_forecast(capsys):
    """Test that a roster for a team not given is a command line error naming it."""
    argv = ['A', 'B', '--roster', 'D=d1', '--roster', 'C=c1', '--roster', 'A=a1']
    assert '--roster for teams not forecast: C, D' in parse_error(predict, argv, capsys)


@pytest.mark.parametrize('value', ['Team A', 'Team A=', '=a1,a2', 'Team A= , '])
def test_roster_format(value, capsys):
    """Test that a roster needs a team, an equals sign and at least one player."""
    assert 'expected TEAM=PLAYER,PLAYER,...' in parse_error(predict, ['A', 'B', '--roster', value], capsys)


def test_roster_parsed():
    """Test that rosters are parsed into a team and its players, spaces trimmed."""
    args = predict.parse_args(['Team A', 'B', '--roster', ' Team A = a1, a2,a3 ', '--roster', 'B=b1'])
    assert args.roster == [('Team A', ('a1', 'a2', 'a3')), ('B', ('b1',))]


def test_when_is_the_start_of_a_day_in_utc():
    """Test that `--when` is the start of that day in UTC."""
    assert predict.parse_args(['A', 'B', '--when', '2026-12-09']).when == dt.datetime(2026, 12, 9, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ('option', 'value', 'error'),
    [('--best-of', '4', 'odd number'), ('--best-of', '-1', 'odd number'), ('--workers', '0', 'at least 1')],
)
def test_counts_checked(option, value, error, capsys):
    """Test that the series length must be odd and positive, and the workers at least 1."""
    assert error in parse_error(predict, ['--tune', option, value], capsys)


def test_best_of_one_is_allowed():
    """Test that a single game is a valid series length."""
    assert predict.parse_args(['--tune', '--best-of', '1']).best_of == 1


def test_help_describes_the_command(capsys):
    """Test that `--help` opens with the first line of the module docstring."""
    assert help_opens_with_docstring(predict, capsys)


def test_module_entry_point(history, monkeypatch):
    """Test that `python -m edh.predictions.predict` exits with main's code."""
    assert run_as_main(monkeypatch, 'edh.predictions.predict', ['Team A', 'Team B'], _history) == 1  # no tuning
