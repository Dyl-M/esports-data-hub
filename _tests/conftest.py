"""Pytest configuration and shared fixtures for Esports Data Hub tests."""

# Standard library
from pathlib import Path

# Third-party
import pytest

# Local
from edh import paths
from edh.scenarios.models import LeagueConfig, QualificationSlot, Series, Tiebreaker

# Test data directory
FIXTURES_DIR = Path(__file__).parent / 'fixtures'
CDF_DAY2_CSV = FIXTURES_DIR / 'cdf_slash_2026_day2.csv'


def make_series(round_, team_1, team_2, score_1=0, score_2=0, **kwargs):
    """Build a series; `finished` (keyword) defaults to True when a score is given, `goals` to None."""
    finished = kwargs.get('finished', score_1 > 0 or score_2 > 0)
    return Series(round_, team_1, team_2, score_1, score_2, finished, kwargs.get('goals'))


def make_league(tiebreakers=('matchscore', 'gamediff'), *, best_of=5, qualification=None, **kwargs):
    """Build a league format from tiebreaker identifiers."""
    slots = tuple(QualificationSlot(label, first, last) for label, (first, last) in (qualification or {}).items())
    return LeagueConfig(
        slug=kwargs.pop('slug', 'test_league'),
        name=kwargs.pop('name', 'Test League'),
        wiki='rocketleague',
        pagename='Test/League',
        best_of=best_of,
        tiebreakers=tuple(Tiebreaker.parse(value) for value in tiebreakers),
        qualification=slots,
        **kwargs,
    )


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Redirect paths.DATA_DIR to a temporary folder."""
    monkeypatch.setattr(paths, 'DATA_DIR', tmp_path)
    return tmp_path


@pytest.fixture
def three_way_tie():
    """A-B-C each 2-1 (A>B>C>A between them), all beating D; full game diff ties A and B.

    Mini-league game diff: A +2, B 0, C -2. Full game diff: A +3, B +3, C +1, D -7.
    """
    return [
        make_series(1, 'A', 'B', 3, 0, goals=((2, 0), (1, 0), (3, 1))),
        make_series(1, 'C', 'D', 3, 0, goals=((1, 0), (2, 1), (4, 0))),
        make_series(2, 'B', 'C', 3, 0, goals=((3, 2), (1, 0), (2, 1))),
        make_series(2, 'A', 'D', 3, 2, goals=((1, 0), (0, 1), (2, 0), (0, 3), (5, 4))),
        make_series(3, 'C', 'A', 3, 2, goals=((1, 0), (0, 1), (2, 1), (0, 2), (3, 0))),
        make_series(3, 'B', 'D', 3, 0, goals=((1, 0), (1, 0), (1, 0))),
    ]
