"""Tests for edh/predictions/evaluate.py - evaluation command line and report."""

# Standard library
import datetime as dt
import json
import logging
import math
import runpy
import sys

# Third-party
import pytest

# Local
from edh import paths
from edh.predictions import evaluate
from edh.predictions.evaluation import Prediction, score
from edh.predictions.tuning import Result, Trial

from .conftest import make_match, make_opponent

START = dt.datetime(2025, 6, 1, 18, tzinfo=dt.UTC)


def _history():
    """A small history over RLCS 2025 and 2026: Team A beats Team B 3-1 every week."""
    return [
        make_match(
            make_opponent('Team A', 3, 'a1 a2 a3'),
            make_opponent('Team B', 1, 'b1 b2 b3'),
            match_id=f'm{week:02d}',
            played_at=START + dt.timedelta(weeks=week),
        )
        for week in range(30)
    ]


@pytest.fixture
def history(monkeypatch, tmp_path):
    """The small history as the cache, `_data/` in a temporary folder."""
    monkeypatch.setattr(evaluate, 'read_history', _history)
    monkeypatch.setattr(paths, 'DATA_DIR', tmp_path)
    return tmp_path


def test_main_writes_the_report(history, caplog):
    """Test a run: the comparison logged, `evaluation.json` and `calibration.csv` written in the last day's folder."""
    with caplog.at_level(logging.INFO):
        assert evaluate.main(['--models', 'uniform', 'elo', '--identities', 'name', 'players', '--workers', '1']) == 0
    folder = history / 'predictions' / (START + dt.timedelta(weeks=29)).date().isoformat()
    report = json.loads((folder / 'evaluation.json').read_text(encoding='utf-8'))
    assert (report['tuning_season'], report['evaluation_season'], report['first_team_share']) == (2025, 2026, 1.0)
    assert 'The first team listed won 100.0% of these series' in caplog.text
    assert [(row['model'], row['identity']) for row in report['results']] == [
        ('uniform', 'name'),
        ('uniform', 'players'),
        ('elo', 'name'),
        ('elo', 'players'),
    ]
    elo = report['results'][2]
    assert set(elo['params']) == {'k', 'shrink', 'offset'}
    assert set(elo['evaluation']) == {'all', 'tier 1', 'experienced'}  # 10+ matches by RLCS 2026
    assert set(elo['at_bound']) <= set(elo['params'])
    assert elo['trials'][0]['params'] == {'k': 16, 'shrink': 0, 'offset': 0}  # the grid's first point
    assert min(trial['log_loss'] for trial in elo['trials']) == elo['tuning']['log_loss']
    assert report['results'][0]['trials'] == [{'params': {}, 'log_loss': pytest.approx(math.log(2))}]
    calibration = (folder / 'calibration.csv').read_text(encoding='utf-8').splitlines()
    assert calibration[0] == 'model,identity,low,high,matches,predicted,observed'
    assert calibration[1].startswith('uniform,name,0.5,0.6,')
    assert 'RLCS 2026 (tuned on RLCS 2025)' in caplog.text
    assert caplog.text.index('elo ') < caplog.text.index('uniform ')  # best log loss first


def test_rerun_replaces_the_report(history):
    """Test that a second run on the same history writes over the first one's report."""
    assert evaluate.main(['--models', 'uniform', '--identities', 'name', '--workers', '1']) == 0
    assert evaluate.main(['--models', 'uniform', '--identities', 'lineup', '--workers', '1']) == 0
    reports = list((history / 'predictions').glob('*/evaluation.json'))
    assert len(reports) == 1
    report = reports[0].read_text(encoding='utf-8')
    assert [row['identity'] for row in json.loads(report)['results']] == ['lineup']


def test_help_describes_the_command(capsys):
    """Test that `--help` opens with the first line of the module docstring."""
    with pytest.raises(SystemExit):
        evaluate.parse_args(['--help'])
    help_text: str = capsys.readouterr().out
    assert evaluate.__doc__.splitlines()[0] in help_text


@pytest.mark.parametrize('workers', ['0', '-2'])
def test_workers_must_be_positive(workers, capsys):
    """Test that a worker count below 1 is a command line error."""
    with pytest.raises(SystemExit):
        evaluate.parse_args(['--workers', workers])
    error: str = capsys.readouterr().err
    assert f'must be at least 1, got {workers}' in error


def test_main_without_history(monkeypatch, caplog):
    """Test that an empty cache is an error pointing to the history command."""
    monkeypatch.setattr(evaluate, 'read_history', list)
    assert evaluate.main([]) == 1
    assert 'python -m edh.predictions.run' in caplog.text


def test_summary_parameters():
    """Test that a model without hyperparameters shows a dash."""
    prediction = Prediction('m', 2026, '1', True, 0.5, 0.5, (3, 1))
    result = Result('uniform', 'name', {}, score([prediction]), (prediction,), (), ())
    assert evaluate.summary([result])[1].endswith('  -')
    assert len(evaluate.summary([])) == 1  # the header only: no series to count


def test_summary_stars_parameters_on_a_bound():
    """Test that the runs are counted and a hyperparameter picked on a bound is starred."""
    prediction = Prediction('m', 2026, '1', True, 0.6, 0.6, (3, 1))
    trials = (Trial({'k': 4, 'shrink': 0.5}, 0.6), Trial({'k': 8, 'shrink': 0.5}, 0.5))
    result = Result('elo', 'name', {'k': 4, 'shrink': 0.5}, score([prediction]), (prediction,), trials, ('k',))
    assert evaluate.summary([result])[1].endswith('    2  k=4*, shrink=0.5')


def test_module_entry_point(history, monkeypatch):
    """Test that `python -m edh.predictions.evaluate` exits with main's code."""
    monkeypatch.setattr(sys, 'argv', ['evaluate', '--models', 'uniform', '--identities', 'name'])
    monkeypatch.setattr('edh.predictions.history.read_history', _history)
    monkeypatch.delitem(sys.modules, 'edh.predictions.evaluate')  # run a fresh copy as __main__
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module('edh.predictions.evaluate', run_name='__main__')
    assert exit_info.value.code == 0
