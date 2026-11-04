"""Tests for edh/scenarios/report.py - console table and outlook export."""

# Standard library
import csv
import json

# Third-party
import pytest

# Local
from edh.scenarios.engine import evaluate
from edh.scenarios.report import format_table, write_outlook

from .conftest import make_league, make_series


def _report(method='exhaustive'):
    """Evaluate a three-team league with one series left (C vs A)."""
    series = [make_series(1, 'A', 'B', 3, 0), make_series(2, 'B', 'C', 3, 0), make_series(3, 'C', 'A')]
    league = make_league(qualification={'Top': (1, 1), 'Top 3': (1, 3)})
    return evaluate(league, series, method=method, samples=200, seed=1)


def test_format_table_exact():
    """Test the exact table: header, one row per team, IN / OUT for settled outcomes."""
    table = format_table(_report())
    lines = table.splitlines()
    assert lines[0] == 'Test League: 1 series left, 6 scenarios (exact)'
    rows = {line.split()[0]: line for line in lines[3:6]}
    assert set(rows) == {'A', 'B', 'C'}
    assert 'IN' in rows['C']  # top 3 of 3 teams
    assert '(+16.7%)' in rows['A']  # top spot decided by goal difference in the 3-0 scenario
    assert rows['A'].split()[2:4] == ['#1', '[88.9%]']  # projected rank, highlighted
    assert rows['C'].split()[2] == '#3'
    assert table.endswith('\n')


def test_format_table_estimated():
    """Test that Monte Carlo tables say the shares are estimated and never claim IN / OUT."""
    table = format_table(_report('montecarlo'))
    assert 'estimated, Monte Carlo' in table.splitlines()[0]
    assert ' IN' not in table


def test_write_outlook(tmp_path):
    """Test the CSV and JSON exports."""
    csv_path, json_path = write_outlook(_report(), tmp_path / 'out')
    rows = list(csv.DictReader(csv_path.open(encoding='utf-8')))
    assert [row['team'] for row in rows] == ['A', 'B', 'C']
    assert {'projected_rank', 'rank_1', 'rank_3', 'Top_qualified', 'Top_undecided', 'unresolved'} <= set(rows[0])
    assert not {'likely_rank', 'avg_rank_position'} & set(rows[0])
    assert [row['projected_rank'] for row in rows] == ['1', '2', '3']
    data = json.loads(json_path.read_text(encoding='utf-8'))
    assert (data['method'], data['scenarios'], len(data['teams'])) == ('exhaustive', 6, 3)
    first = data['teams'][0]
    assert first['projected_rank'] == 1
    assert first['expected_rank'] == pytest.approx(7 / 6)
