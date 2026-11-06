"""Tests for edh/scenarios/run.py - command line entry point."""

# Standard library
import io
import json
import runpy
import shutil
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import date, timedelta

# Third-party
import pytest

# Local
from edh.exceptions import VisualError
from edh.scenarios import run
from edh.scenarios.models import EventInfo, Logos
from edh.scenarios.report import read_outlook, write_outlook
from edh.scenarios.sources import CsvSource, LiquipediaSource, write_event

from .conftest import CDF_DAY2_CSV, make_series

NEO = EventInfo(organizers=('NEO',))

LEAGUE = {
    'name': 'Test League',
    'wiki': 'rocketleague',
    'pagename': 'Test/League',
    'best_of': 5,
    'tiebreakers': ['matchscore', 'gamediff'],
    'qualification': {'Top': [1, 1]},
}


class StaticSource:
    """Result source serving a fixed list of series and event details, standing in for Liquipedia."""

    def __init__(self, series, event=NEO):
        """Serve `series` and `event`; the leagues asked for are recorded in `configs` / `event_configs`."""
        self.series = series
        self.event = event
        self.configs = []
        self.event_configs = []

    def fetch(self, config):
        """Record the league asked for, then return the stored series."""
        self.configs.append(config)
        return self.series

    def fetch_event(self, config):
        """Record the league asked for, then return the stored event details."""
        self.event_configs.append(config)
        return self.event


class CardRecorder:
    """Stands in for `export_cards`, recording its calls instead of starting a browser."""

    def __init__(self, error=None):
        """Start with no call; raise `error` on every call when set."""
        self.calls = []
        self.error = error

    def __call__(self, report, config, event, folder, *, as_of):
        """Record the call, then fail with the error or return the paths a real export would write."""
        self.calls.append({'report': report, 'config': config, 'event': event, 'folder': folder, 'as_of': as_of})
        if self.error:
            raise self.error
        return [folder / 'card_portrait.png']


@pytest.fixture
def league_folder(data_dir):
    """Create a test league with the CDF snapshot as its results."""
    folder = data_dir / 'test_league'
    folder.mkdir()
    (folder / 'league.json').write_text(json.dumps(LEAGUE), encoding='utf-8')
    shutil.copy(CDF_DAY2_CSV, folder / 'results.csv')
    return folder


def run_folder(league_folder, method='montecarlo', day=None):
    """Outlook folder of a run of `method` on `day` (today by default)."""
    return league_folder / 'outlook' / (day or date.today()).isoformat() / method


def test_parse_args_defaults():
    """Test the default command line options."""
    args = run.parse_args(['cdf_slash_2026'])
    assert (args.league, args.source, args.method, args.seed) == ('cdf_slash_2026', 'liquipedia', 'auto', None)
    assert args.workers is None
    assert run.parse_args(['cdf_slash_2026', '--workers', '4']).workers == 4


def test_main_csv_source(league_folder):
    """Test a full Monte Carlo run from the CSV snapshot: table printed, outlook saved, results untouched."""
    before = (league_folder / 'results.csv').read_text(encoding='utf-8')
    out = io.StringIO()  # rather than capsys, whose `CaptureResult` PyCharm types as `str | bytes`
    with redirect_stdout(out):
        assert run.main(['test_league', '--source', 'csv', '--samples', '200', '--seed', '3']) == 0
    assert 'Test League: 23 series left, 200 scenarios' in out.getvalue()
    assert (run_folder(league_folder) / 'outlook.csv').is_file()
    assert (run_folder(league_folder) / 'outlook.json').is_file()
    assert (league_folder / 'results.csv').read_text(encoding='utf-8') == before


def test_main_liquipedia_source_saves_snapshot(league_folder, monkeypatch):
    """Test that fetched Liquipedia results and event details are saved as snapshots, and no card is rendered."""
    fetched = [make_series(1, 'A', 'B', 3, 0), make_series(2, 'A', 'B')]
    source = StaticSource(fetched)
    recorder = CardRecorder()
    monkeypatch.setattr(run, 'build_source', lambda name: source)
    monkeypatch.setattr(run, 'export_cards', recorder)
    assert run.main(['test_league']) == 0
    league = run.load_league('test_league')
    assert source.configs == source.event_configs == [league]
    assert CsvSource().fetch(league) == fetched
    assert CsvSource.fetch_event(league) == NEO
    assert not recorder.calls


def test_main_dates_the_run_by_the_last_finished_series(league_folder, monkeypatch):
    """Test that the card and the outlook folder are dated by the last day a series finished, not the run's day."""
    day_1, day_2, day_3 = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)
    fetched = [
        make_series(1, 'A', 'B', 3, 0, played_on=day_1),
        make_series(2, 'A', 'B', 3, 1, played_on=day_2),
        make_series(3, 'A', 'B', 1, 0, finished=False, played_on=day_3),  # in progress: not counted
    ]
    recorder = CardRecorder()
    monkeypatch.setattr(run, 'build_source', lambda name: StaticSource(fetched))
    monkeypatch.setattr(run, 'export_cards', recorder)
    assert run.main(['test_league', '--visual']) == 0
    assert (recorder.calls[0]['as_of'], recorder.calls[0]['folder']) == (
        day_2,
        run_folder(league_folder, 'exhaustive', day_2),
    )
    assert (run_folder(league_folder, 'exhaustive', day_2) / 'outlook.json').is_file()
    assert run.main(['test_league', '--visual-only']) == 0
    assert recorder.calls[1]['as_of'] == day_2


@pytest.mark.parametrize(
    ('series', 'day'),
    [
        (
            [make_series(1, 'A', 'B', 3, 0, played_on=date(2026, 11, 3)), make_series(1, 'A', 'B', 3, 0)],
            date(2026, 11, 3),
        ),
        ([make_series(1, 'A', 'B', played_on=date(2026, 11, 3))], date.today()),  # nothing finished yet
        ([make_series(1, 'A', 'B', 3, 0)], date.today()),  # snapshot without days
    ],
)
def test_league_day(series, day, caplog):
    """Test the day results are as of: the latest finished series' day, today (with a warning) when none is known."""
    assert run.league_day(series) == day
    assert ('results dated today' in caplog.text) == (day == date.today())


def test_parse_args_visual_options_are_exclusive():
    """Test that --visual and --visual-only cannot be combined."""
    assert run.parse_args(['x', '--visual']).visual
    assert run.parse_args(['x', '--visual-only']).visual_only
    err = io.StringIO()
    with redirect_stderr(err), pytest.raises(SystemExit):
        run.parse_args(['x', '--visual', '--visual-only'])
    assert 'not allowed with' in err.getvalue()


def test_main_visual_renders_the_evaluated_report(league_folder, monkeypatch):
    """Test that --visual renders the card from the fresh report, dated today, into the run's outlook folder."""
    recorder = CardRecorder()
    monkeypatch.setattr(run, 'export_cards', recorder)
    assert run.main(['test_league', '--source', 'csv', '--samples', '100', '--seed', '1', '--visual']) == 0
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert (call['folder'], call['as_of']) == (run_folder(league_folder), date.today())
    assert call['report'] == read_outlook(run_folder(league_folder) / 'outlook.json')
    assert call['event'] == EventInfo()  # csv source without an event snapshot


def test_main_visual_only_uses_saved_outlook(league_folder, monkeypatch):
    """Test that --visual-only reads the saved outlook and event snapshot, without fetching results."""
    assert run.main(['test_league', '--source', 'csv', '--samples', '100', '--seed', '1']) == 0
    write_event(NEO, league_folder / 'event.json')
    (league_folder / 'results.csv').unlink()  # would fail any fetch
    recorder = CardRecorder()
    monkeypatch.setattr(run, 'export_cards', recorder)
    assert run.main(['test_league', '--visual-only']) == 0
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call['event'] == NEO
    assert call['report'].scenarios == 100
    assert call['as_of'] == date.today()
    assert call['folder'] == run_folder(league_folder)


def test_main_keeps_each_method_and_day(league_folder, monkeypatch):
    """Test that runs of other methods and days are kept, and --visual-only picks the latest (of --method)."""
    assert run.main(['test_league', '--source', 'csv', '--samples', '100', '--seed', '1']) == 0
    report = read_outlook(run_folder(league_folder) / 'outlook.json')
    yesterday = date.today() - timedelta(days=1)
    write_outlook(report, run_folder(league_folder, day=yesterday))
    write_outlook(replace(report, method='exhaustive'), run_folder(league_folder, 'exhaustive', yesterday))
    write_outlook(report, league_folder / 'outlook' / 'notes' / 'montecarlo')  # not a run folder (no day): ignored
    assert run.main(['test_league', '--source', 'csv', '--samples', '100', '--seed', '2']) == 0
    assert read_outlook(run_folder(league_folder, day=yesterday) / 'outlook.json') == report  # not overwritten
    recorder = CardRecorder()
    monkeypatch.setattr(run, 'export_cards', recorder)
    assert run.main(['test_league', '--visual-only']) == 0
    assert run.main(['test_league', '--visual-only', '--method', 'exhaustive']) == 0
    assert len(recorder.calls) == 2
    latest, exhaustive = recorder.calls[0], recorder.calls[1]
    assert (latest['folder'], latest['as_of'], latest['report'].method) == (
        run_folder(league_folder),
        date.today(),
        'montecarlo',
    )
    assert (exhaustive['folder'], exhaustive['as_of']) == (
        run_folder(league_folder, 'exhaustive', yesterday),
        yesterday,
    )
    assert run.main(['test_league', '--visual-only', '--method', 'winnersfirst']) == 1  # never saved


def test_main_liquipedia_source_downloads_logos(league_folder, monkeypatch):
    """Test that a Liquipedia run downloads the event's logos and that the card receives them."""
    event = EventInfo(organizers=('NEO',), logo_url='https://x/cup.png')
    downloads = []

    def download(fetched, folder):
        """Stand in for download_logos: write the logo and its manifest as a real download would."""
        downloads.append((fetched, folder))
        folder.mkdir()
        (folder / 'event.png').write_bytes(b'cup')
        manifest = {'event': {'url': fetched.logo_url, 'file': 'event.png'}, 'teams': {}}
        (folder / 'logos.json').write_text(json.dumps(manifest), encoding='utf-8')

    recorder = CardRecorder()
    monkeypatch.setattr(run, 'build_source', lambda name: StaticSource([make_series(1, 'A', 'B')], event))
    monkeypatch.setattr(run, 'download_logos', download)
    monkeypatch.setattr(run, 'export_cards', recorder)
    assert run.main(['test_league', '--visual']) == 0
    assert downloads == [(event, league_folder / 'logos')]
    assert recorder.calls[0]['event'].logos == Logos(event=league_folder / 'logos' / 'event.png')
    assert CsvSource.fetch_event(run.load_league('test_league')) == NEO  # addresses are not saved in event.json


def test_main_visual_only_without_outlook(league_folder):
    """Test that --visual-only fails cleanly when the league was never evaluated."""
    assert run.main(['test_league', '--visual-only']) == 1


def test_main_visual_error_exits_with_code_1(league_folder, monkeypatch):
    """Test that a rendering failure exits with code 1 after the outlook is saved."""
    recorder = CardRecorder(error=VisualError('no browser'))
    monkeypatch.setattr(run, 'export_cards', recorder)
    assert run.main(['test_league', '--source', 'csv', '--samples', '100', '--visual']) == 1
    assert len(recorder.calls) == 1
    assert (run_folder(league_folder) / 'outlook.json').is_file()


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
