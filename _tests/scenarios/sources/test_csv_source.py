"""Tests for edh/scenarios/sources/csv_source.py - CSV results reading and writing."""

# Standard library
from datetime import date

# Third-party
import pytest

# Local
from edh.exceptions import DataSourceError
from edh.scenarios.sources import CsvSource, write_results

from ...conftest import CDF_DAY2_CSV, make_league, make_series

HEADER = 'round,team_1,team_2,score_1,score_2,finished,goals\n'


def test_fetch_cdf_snapshot():
    """Test the Coupe de France Slash 2026 snapshot after Day 2's third series started."""
    series = CsvSource(CDF_DAY2_CSV).fetch(make_league())
    assert len(series) == 30
    assert sum(item.finished for item in series) == 7
    in_progress = series[7]
    assert (in_progress.teams, in_progress.score_1, in_progress.score_2) == (('Karmine Corp', 'DUMORNAY GOAT'), 1, 1)
    assert not in_progress.finished
    assert in_progress.goals == ((3, 2), (0, 3))
    assert series[8].goals is None


def test_write_then_fetch_round_trip(tmp_path):
    """Test that written results read back unchanged, missing parent folders created."""
    series = CsvSource(CDF_DAY2_CSV).fetch(make_league())
    path = tmp_path / 'new_league' / 'snapshots' / 'results.csv'
    write_results(series, path)
    assert CsvSource(path).fetch(make_league()) == series


def test_fetch_missing_file(tmp_path):
    """Test that a missing file is a data source error."""
    with pytest.raises(DataSourceError, match='not found'):
        CsvSource(tmp_path / 'absent.csv').fetch(make_league())


@pytest.mark.parametrize(
    ('content', 'message'),
    [
        pytest.param('round,team_1\n1,A\n', 'missing columns', id='missing-columns'),
        pytest.param(HEADER + 'x,A,B,0,0,0,\n', 'line 2', id='bad-round'),
        pytest.param(HEADER + '1,A,A,0,0,0,\n', 'line 2', id='same-team'),
        pytest.param(HEADER + '1,A,B,3,0,1,3-0;bad\n', 'line 2', id='bad-goals'),
        pytest.param(HEADER.rstrip('\n') + ',date\n1,A,B,3,0,1,,4/11\n', 'line 2', id='bad-day'),
    ],
)
def test_fetch_invalid_file(tmp_path, content, message):
    """Test that a file without the expected columns is rejected, and an invalid row reported with its line number."""
    path = tmp_path / 'results.csv'
    path.write_text(content, encoding='utf-8')
    with pytest.raises(DataSourceError, match=message):
        CsvSource(path).fetch(make_league())


def test_round_trip_keeps_known_empty_goals(tmp_path):
    """Test that a walkover's known-empty goals survive the CSV round trip (not read back as unknown)."""
    walkover = make_series(1, 'A', 'B', 3, 0, goals=())
    unknown = make_series(2, 'A', 'B', 3, 0)
    path = tmp_path / 'results.csv'
    write_results([walkover, unknown], path)
    assert path.read_text(encoding='utf-8').splitlines()[1:] == ['1,A,B,3,0,1,-,', '2,A,B,3,0,1,,']
    assert CsvSource(path).fetch(make_league()) == [walkover, unknown]


def test_round_trip_keeps_days(tmp_path):
    """Test that the day of each series is written as `YYYY-MM-DD` and read back, empty when unknown."""
    played = make_series(1, 'A', 'B', 3, 0, played_on=date(2026, 11, 4))
    unknown = make_series(2, 'A', 'B')
    path = tmp_path / 'results.csv'
    write_results([played, unknown], path)
    assert path.read_text(encoding='utf-8').splitlines() == [
        'round,team_1,team_2,score_1,score_2,finished,goals,date',
        '1,A,B,3,0,1,,2026-11-04',
        '2,A,B,0,0,0,,',
    ]
    assert CsvSource(path).fetch(make_league()) == [played, unknown]


def test_fetch_empty_scores_are_zero(tmp_path):
    """Test that an upcoming series with empty score cells reads as 0-0."""
    path = tmp_path / 'results.csv'
    path.write_text(HEADER + '1,A,B,,,0,\n', encoding='utf-8')
    assert CsvSource(path).fetch(make_league()) == [make_series(1, 'A', 'B')]


def test_fetch_without_date_column(tmp_path):
    """Test that a snapshot written before the `date` column existed still reads, days unknown."""
    path = tmp_path / 'results.csv'
    path.write_text(HEADER + '1,A,B,3,0,1,\n', encoding='utf-8')
    assert CsvSource(path).fetch(make_league()) == [make_series(1, 'A', 'B', 3, 0)]


def test_fetch_league_snapshot(data_dir):
    """Test that without a file, the source reads the league's snapshot in _data/<league>/results.csv."""
    league = make_league(slug='some_league')
    series = [make_series(1, 'A', 'B', 3, 0), make_series(2, 'A', 'B')]
    write_results(series, data_dir / 'some_league' / 'results.csv')
    assert CsvSource().fetch(league) == series


def test_fetch_missing_league_snapshot(data_dir):
    """Test that a league without a snapshot is a data source error naming the expected file."""
    with pytest.raises(DataSourceError, match=r'no_snapshot.results\.csv'):
        CsvSource().fetch(make_league(slug='no_snapshot'))
