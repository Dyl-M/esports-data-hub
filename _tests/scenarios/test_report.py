"""Tests for edh/scenarios/report.py - console table and outlook export."""

# Standard library
import csv
import json
from dataclasses import replace

# Third-party
import pytest

# Local
from edh.exceptions import DataSourceError
from edh.scenarios.engine import evaluate
from edh.scenarios.models import EvaluationOptions
from edh.scenarios.report import format_share, format_table, read_outlook, write_outlook

from ..conftest import make_league, make_series


def _report(method='exhaustive'):
    """Evaluate a three-team league with one series left (C vs A)."""
    series = [make_series(1, 'A', 'B', 3, 0), make_series(2, 'B', 'C', 3, 0), make_series(3, 'C', 'A')]
    league = make_league(qualification={'Top': (1, 1), 'Top 3': (1, 3)})
    return evaluate(league, series, EvaluationOptions(method=method, samples=200, seed=1))


def test_format_table_exact():
    """Test the exact table: header, one row per team, IN / OUT for settled outcomes."""
    table = format_table(_report())
    lines = table.splitlines()
    assert lines[0] == 'Test League: 1 series left, 6 scenarios (exact)'
    assert lines[2].split()[:7] == ['Team', 'Avg', 'Proj', '#1', '#2', '#3', 'Top']  # one column per rank
    rows = {line.split()[0]: line for line in lines[3:6]}
    assert set(rows) == {'A', 'B', 'C'}
    assert 'IN' in rows['C']  # top 3 of 3 teams
    # the top spot is a three-way tie in the 3-0 scenario: a third of it counts, as in the #1 share
    assert '88.9% (16.7% tied)' in rows['A']
    assert rows['A'].split()[2:4] == ['#1', '[88.9%]']  # projected rank, highlighted
    assert rows['C'].split()[2] == '#3'
    assert rows['C'].split()[5].startswith('[')  # the last rank cell, C's projected rank
    assert table.endswith('\n')


def test_format_table_estimated():
    """Test that Monte Carlo tables say the shares are estimated and never claim IN / OUT."""
    table = format_table(_report('montecarlo'))
    assert 'estimated, Monte Carlo' in table.splitlines()[0]
    assert ' IN' not in table
    assert 'OUT' not in table


def test_write_outlook(tmp_path):
    """Test the CSV and JSON exports."""
    csv_path, json_path = write_outlook(replace(_report(), league='Ligue Été'), tmp_path / 'out')
    assert b'\r' not in csv_path.read_bytes() + json_path.read_bytes()  # LF on every platform
    rows = list(csv.DictReader(csv_path.read_text(encoding='utf-8').splitlines()))
    assert [row['team'] for row in rows] == ['A', 'B', 'C']
    assert {'projected_rank', 'Top_qualified', 'Top_undecided', 'unresolved'} <= set(rows[0])
    assert [column for column in rows[0] if column.startswith('rank_')] == ['rank_1', 'rank_2', 'rank_3']
    assert not {'likely_rank', 'avg_rank_position'} & set(rows[0])
    assert [row['projected_rank'] for row in rows] == ['1', '2', '3']
    data = json.loads(json_path.read_text(encoding='utf-8'))
    assert 'Ligue Été' in json_path.read_text(encoding='utf-8')  # written as is, not as a JSON escape
    assert (data['method'], data['scenarios'], len(data['teams'])) == ('exhaustive', 6, 3)
    first = data['teams'][0]
    assert first['projected_rank'] == 1
    assert first['expected_rank'] == pytest.approx(7 / 6)


def test_format_table_eliminated_team():
    """Test that an exact report marks a team that can no longer reach a slot as OUT."""
    series = [make_series(1, 'A', 'B', 3, 0), make_series(2, 'A', 'C', 3, 0), make_series(3, 'B', 'C')]
    report = evaluate(make_league(qualification={'Top': (1, 1)}), series)
    rows = {line.split()[0]: line for line in format_table(report).splitlines()[3:6]}
    assert rows['A'].split()[-2:] == ['IN', '-']
    assert 'OUT' in rows['B']
    assert 'OUT' in rows['C']


@pytest.mark.parametrize(
    ('share', 'text'),
    [
        (0, '-'),
        (1, '100%'),
        (0.5, '50.0%'),
        (0.0001, '<0.1%'),
        (0.001, '0.1%'),
        (0.999, '99.9%'),
        (0.99999, '>99.9%'),
    ],
)
def test_format_share(share, text):
    """Test that 0% and 100% are kept for exact values, and shares beyond 0.1%-99.9% marked `<` / `>`."""
    assert format_share(share) == text


def test_read_outlook_round_trip(tmp_path):
    """Test that an exported outlook reads back as the same report."""
    report = _report('montecarlo')
    _, json_path = write_outlook(report, tmp_path)
    assert read_outlook(json_path) == report


def test_read_outlook_missing_file(tmp_path):
    """Test that a missing outlook says to evaluate the league first."""
    with pytest.raises(DataSourceError, match='evaluate the league first'):
        read_outlook(tmp_path / 'outlook.json')


@pytest.mark.parametrize(
    'content',
    [
        '{not json',
        '[]',
        '{"league": "x"}',
        '{"league": "x", "method": "m", "scenarios": "many", "remaining": 1, "teams": []}',
    ],
)
def test_read_outlook_invalid_file(tmp_path, content):
    """Test that malformed outlooks are data source errors."""
    path = tmp_path / 'outlook.json'
    path.write_text(content, encoding='utf-8')
    with pytest.raises(DataSourceError, match='Invalid outlook file'):
        read_outlook(path)
