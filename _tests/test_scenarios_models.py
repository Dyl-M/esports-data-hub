"""Tests for edh/scenarios/models.py - series, league format and outcome models."""

# Third-party
import pytest

# Local
from edh.scenarios.models import QualificationSlot, RankGroup, ScenarioReport, Series, TeamOutlook, Tiebreaker

from .conftest import make_league

# === Series ===


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'round': 0}, 'round'),
        ({'team_1': ''}, 'empty'),
        ({'team_2': 'A'}, 'itself'),
        ({'score_1': -1}, 'scores'),
    ],
)
def test_series_validation(kwargs, message):
    """Test that invalid series are rejected."""
    values = {'round': 1, 'team_1': 'A', 'team_2': 'B'} | kwargs
    with pytest.raises(ValueError, match=message):
        Series(**values)


def test_series_renamed():
    """Test that aliases map old team names to current ones."""
    series = Series(1, 'Old', 'B', 3, 0, finished=True)
    assert series.renamed({'Old': 'New'}).teams == ('New', 'B')
    assert series.renamed({}).teams == ('Old', 'B')


# === Tiebreaker ===


@pytest.mark.parametrize(
    ('value', 'name', 'context'),
    [('gamediff', 'gamediff', 'full'), ('ml.gamediff', 'gamediff', 'ml'), ('manual', 'manual', 'full')],
)
def test_tiebreaker_parse(value, name, context):
    """Test that Liquipedia identifiers are parsed and printed back unchanged."""
    tiebreaker = Tiebreaker.parse(value)
    assert (tiebreaker.name, tiebreaker.context) == (name, context)
    assert str(tiebreaker) == value


@pytest.mark.parametrize(
    ('value', 'message'),
    [('buchholz', 'Unsupported tiebreaker'), ('h2hcs.gamediff', 'context'), ('ml.manual', 'mini-league')],
)
def test_tiebreaker_rejects_unsupported(value, message):
    """Test that unsupported identifiers and contexts are rejected."""
    with pytest.raises(ValueError, match=message):
        Tiebreaker.parse(value)


# === League format ===


@pytest.mark.parametrize(('first', 'last'), [(0, 4), (5, 4)])
def test_qualification_slot_rejects_bad_range(first, last):
    """Test that rank ranges must be 1-based and ordered."""
    with pytest.raises(ValueError, match='rank range'):
        QualificationSlot('Playoffs', first, last)


def test_qualification_slot_rejects_empty_label():
    """Test that a slot needs a label."""
    with pytest.raises(ValueError, match='label'):
        QualificationSlot('', 1, 4)


@pytest.mark.parametrize(('best_of', 'wins_needed'), [(1, 1), (5, 3), (7, 4)])
def test_league_wins_needed(best_of, wins_needed):
    """Test the number of games needed to win a series."""
    assert make_league(best_of=best_of).wins_needed == wins_needed


@pytest.mark.parametrize('best_of', [0, 4])
def test_league_rejects_even_best_of(best_of):
    """Test that series lengths must be positive and odd."""
    with pytest.raises(ValueError, match='best_of'):
        make_league(best_of=best_of)


def test_league_requires_tiebreakers():
    """Test that a league needs at least one tiebreaker."""
    with pytest.raises(ValueError, match='tiebreaker'):
        make_league(tiebreakers=())


def test_league_rejects_negative_walkover_games():
    """Test that walkover games cannot be negative."""
    with pytest.raises(ValueError, match='walkover_games'):
        make_league(walkover_games=-1)


# === Outcomes ===


def test_rank_group_properties():
    """Test the rank range and resolution of a group."""
    assert RankGroup(3, ('A', 'B')).last == 4
    assert not RankGroup(3, ('A', 'B')).resolved
    assert RankGroup(1, ('A',)).resolved


def test_team_outlook_expected_rank():
    """Test the mean rank of a team."""
    outlook = TeamOutlook('A', (0.5, 0.5, 0.0), {}, {}, 0.0)
    assert outlook.expected_rank == pytest.approx(1.5)


@pytest.mark.parametrize(('method', 'exact'), [('exhaustive', True), ('montecarlo', False)])
def test_scenario_report_exact(method, exact):
    """Test that only exhaustive reports are exact."""
    assert ScenarioReport('League', method, 1, 0, ()).exact is exact
