"""Tests for edh/predictions/forecast.py - forecasts of matches not played yet."""

# Standard library
import datetime as dt

# Third-party
import pytest

# Local
from edh.predictions.evaluation import walk_forward
from edh.predictions.forecast import Forecast, Forecaster, Side
from edh.predictions.lineups import identify_lineups
from edh.predictions.ratings import BradleyTerry, Elo, Glicko2

from .conftest import make_match, make_opponent

START = dt.datetime(2025, 5, 1, 18, tzinfo=dt.UTC)  # RLCS 2025 until day 135 (September 13), then RLCS 2026
TEAMS = {'A': 'a1 a2 a3', 'B': 'b1 b2 b3', 'C': 'c1 c2 c3'}
PAIRS = [('A', 'B'), ('A', 'C'), ('B', 'C')]
SCORES = [(3, 1), (2, 3), (3, 0), (1, 3), (3, 2)]
MODELS = [
    lambda: Elo(k=30, shrink=0.3, offset=-50),
    lambda: Glicko2(tau=0.5, start_rd=200, shrink=0.2),
    lambda: BradleyTerry(half_life=60, l2=0.5),
]


def _day(day):
    """The start of a match on day (from START)."""
    return START + dt.timedelta(days=day)


def _history(days=range(0, 200, 4)):
    """A, B and C playing each other in turn on these days, with varied scores, over RLCS 2025 and 2026."""
    matches = []
    for index, day in enumerate(days):
        (first, second), (won, lost) = PAIRS[index % 3], SCORES[index % 5]
        opponents = make_opponent(first, won, TEAMS[first]), make_opponent(second, lost, TEAMS[second])
        matches.append(make_match(*opponents, match_id=f'm{day:03d}', played_at=_day(day)))
    return matches


def _forecaster(matches, identity='name', model=None, day=200):
    """A forecaster on the history before day, with Elo by default."""
    return Forecaster(matches, identify_lineups(matches), identity, model or Elo(k=30, offset=-50), _day(day))


def _forecast(forecaster, first, second):
    """The forecast of two teams, as the forecaster resolves them."""
    return forecaster.forecast(forecaster.side(first), forecaster.side(second))


# === Equal to the walk-forward ===


@pytest.mark.parametrize('identity', ['name', 'lineup', 'players'])
@pytest.mark.parametrize('model', MODELS)
def test_forecast_is_the_walk_forward_prediction(identity, model):
    """Test that a forecast at a match's start gives exactly the probability the walk-forward predicted for it."""
    matches = _history()
    lineups = identify_lineups(matches)
    predicted = {
        prediction.match_id: prediction.game_probability
        for prediction in walk_forward(matches, lineups, identity, model())
    }
    for match in matches[1:]:
        forecaster = Forecaster(matches, lineups, identity, model(), match.played_at)
        first, second = (opponent.name for opponent in match.opponents)
        assert _forecast(forecaster, first, second).game_probability == predicted[match.match_id]


def test_later_matches_are_left_out():
    """Test that matches starting at the forecast's time or later teach nothing."""
    matches = _history()
    later = _forecaster(matches, day=100)
    assert _forecast(later, 'A', 'B') == _forecast(_forecaster(matches[:25], day=100), 'A', 'B')


def test_new_season_pulls_toward_the_mean():
    """Test that members last seen in an earlier season start the forecast's season: fully shrunk, a coin flip."""
    matches = _history(range(0, 120, 4))  # RLCS 2025 only
    model = Elo(k=30, shrink=1)
    assert _forecast(_forecaster(matches, model=model, day=130), 'A', 'B').game_probability != 0.5
    assert _forecast(_forecaster(matches, model=Elo(k=30, shrink=1), day=150), 'A', 'B').game_probability == 0.5


# === Sides ===


def test_side_is_the_latest_in_the_history():
    """Test that a team is resolved to its latest lineup and roster."""
    forecaster = _forecaster(_history())
    assert forecaster.side('C') == Side('C', 2, frozenset({'c1', 'c2', 'c3'}))


@pytest.mark.parametrize('identity', ['name', 'lineup', 'players'])
def test_team_never_seen_is_a_cold_start(identity):
    """Test that a team the history never saw is rated by its name from the starting rating, and reported."""
    forecaster = _forecaster(_history(), identity)
    nobody = forecaster.side('Nobody')
    assert nobody == Side('Nobody', None, frozenset())
    forecast = forecaster.forecast(nobody, forecaster.side('Other'))
    assert (forecast.game_probability, forecast.unseen) == (0.5, (('Nobody',), ('Other',)))


def test_unseen_does_not_depend_on_earlier_forecasts():
    """Test that a cold start is reported in every forecast, not only the first one rating it."""
    forecaster = _forecaster(_history())
    assert (
        _forecast(forecaster, 'Nobody', 'A').unseen == _forecast(forecaster, 'Nobody', 'B').unseen == (('Nobody',), ())
    )


def test_given_roster_replaces_the_players():
    """Test that a given roster is what the players identity rates, a new player reported as a cold start."""
    forecaster = _forecaster(_history(), 'players')
    side = forecaster.side('A', ['a1', 'a2', 'x9'])
    assert side == Side('A', 0, frozenset({'a1', 'a2', 'x9'}))
    assert forecaster.forecast(side, forecaster.side('B')).unseen == (('x9',), ())


def test_given_roster_keeps_the_lineup():
    """Test that a given roster leaves the lineup identity on the team's latest lineup."""
    forecaster = _forecaster(_history(), 'lineup')
    assert forecaster.forecast(forecaster.side('A', ['x7', 'x8', 'x9']), forecaster.side('B')).unseen == ((), ())


def test_series_probability():
    """Test that the series probability follows the game probability and the series length."""
    side = Side('A', None, frozenset())
    assert Forecast(side, side, 0.5, ((), ())).series_probability(3) == 0.5
    assert Forecast(side, side, 0.6, ((), ())).series_probability(3) == pytest.approx(0.68256)
