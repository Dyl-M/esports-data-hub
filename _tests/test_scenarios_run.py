"""Tests for edh/scenarios/run.py - command line entry point."""

# Standard library
import json
import runpy
import shutil
import sys

# Third-party
import pytest

# Local
from edh.scenarios import run
from edh.scenarios.sources import CsvSource, LiquipediaSource

from .conftest import CDF_DAY2_CSV, make_series

LEAGUE = {
    'name': 'Test League',
    'wiki': 'rocketleague',
    'pagename': 'Test/League',
    'best_of': 5,
    'tiebreakers': ['matchscore', 'gamediff'],
    'qualification': {'Top': [1, 1]},
}


class StaticSource:
    """Result source serving a fixed list of series, standing in for Liquipedia."""

    def __init__(self, series):
        """Serve `series` on every fetch; the leagues asked for are recorded in `configs`."""
        self.series = series
        self.configs = []

    def fetch(self, config):
        """Record the league asked for, then return the stored series."""
        self.configs.append(config)
        return self.series


@pytest.fixture
def league_folder(data_dir):
    """Create a test league with the CDF snapshot as its results."""
    folder = data_dir / 'test_league'
    folder.mkdir()
    (folder / 'league.json').write_text(json.dumps(LEAGUE), encoding='utf-8')
    shutil.copy(CDF_DAY2_CSV, folder / 'results.csv')
    return folder


def test_parse_args_defaults():
    """Test the default command line options."""
    args = run.parse_args(['cdf_slash_2026'])
    assert (args.league, args.source, args.method, args.seed) == ('cdf_slash_2026', 'liquipedia', 'auto', None)


def test_main_csv_source(league_folder, capsys):
    """Test a full Monte Carlo run from the CSV snapshot: table printed, outlook saved, results untouched."""
    before = (league_folder / 'results.csv').read_text(encoding='utf-8')
    assert run.main(['test_league', '--source', 'csv', '--samples', '200', '--seed', '3']) == 0
    assert 'Test League: 23 series left, 200 scenarios' in capsys.readouterr().out
    assert (league_folder / 'outlook' / 'outlook.csv').is_file()
    assert (league_folder / 'outlook' / 'outlook.json').is_file()
    assert (league_folder / 'results.csv').read_text(encoding='utf-8') == before


def test_main_liquipedia_source_saves_snapshot(league_folder, monkeypatch):
    """Test that fetched Liquipedia results are saved as the CSV snapshot."""
    fetched = [make_series(1, 'A', 'B', 3, 0), make_series(2, 'A', 'B')]
    source = StaticSource(fetched)
    monkeypatch.setattr(run, 'build_source', lambda name: source)
    assert run.main(['test_league']) == 0
    league = run.load_league('test_league')
    assert source.configs == [league]
    assert CsvSource().fetch(league) == fetched


@pytest.mark.parametrize(
    'argv',
    [['absent'], ['test_league', '--source', 'csv', '--method', 'exhaustive'], ['test_league', '--samples', '0']],
)
def test_main_errors(league_folder, argv, monkeypatch):
    """Test that configuration, source and evaluation errors exit with code 1."""
    monkeypatch.setattr(run, 'build_source', lambda name: CsvSource(league_folder / 'results.csv'))
    assert run.main(argv) == 1


def test_build_source_csv(league_folder):
    """Test that the csv source reads the league's own results snapshot."""
    league = run.load_league('test_league')
    source = run.build_source('csv')
    assert isinstance(source, CsvSource)
    assert source.fetch(league) == CsvSource(league_folder / 'results.csv').fetch(league)


def test_build_source_liquipedia():
    """Test that the default source is Liquipedia."""
    assert isinstance(run.build_source('liquipedia'), LiquipediaSource)


def test_module_entry_point(league_folder, monkeypatch):
    """Test that `python -m edh.scenarios.run` exits with main's code (1 for an unknown league)."""
    monkeypatch.setattr(sys, 'argv', ['run', 'absent'])
    monkeypatch.delitem(sys.modules, 'edh.scenarios.run')  # run a fresh copy as __main__, not the imported one
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module('edh.scenarios.run', run_name='__main__')
    assert exit_info.value.code == 1
