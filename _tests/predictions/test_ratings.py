"""Tests for edh/predictions/ratings.py - rating models."""

# Standard library
import datetime as dt
import math

# Third-party
import pytest

# Local
from edh.predictions.constants import ELO_MEAN, GLICKO2_SCALE, GLICKO2_VOLATILITY
from edh.predictions.ratings import BradleyTerry, Elo, Glicko2, GlickoState, Uniform, glicko2_step

DAY = dt.datetime(2026, 1, 5, 18, tzinfo=dt.UTC)


def _introduced(model, *members, predecessor=None):
    """The model with each member introduced (from `predecessor` when given)."""
    for member in members:
        model.introduce(member, predecessor)
    return model


# === Uniform ===


def test_uniform_is_a_coin_flip():
    """Test that the baseline gives every game 1/2 and learns nothing."""
    model = _introduced(Uniform(), 'a', 'b')
    model.new_season('a')
    model.prepare(DAY)
    model.update(('a',), ('b',), (3, 0), DAY)
    assert model.game_probability(('a',), ('b',)) == 0.5


# === Elo ===


def test_elo_update_per_game():
    """Test that each side moves by k times its games won minus expected, in opposite directions."""
    model = _introduced(Elo(k=20), 'a', 'b')
    model.update(('a',), ('b',), (3, 1), DAY)  # expected 2 of 4 games at equal ratings
    assert model.ratings == {'a': ELO_MEAN + 20, 'b': ELO_MEAN - 20}
    assert model.game_probability(('a',), ('b',)) == pytest.approx(1 / (1 + 10 ** (-40 / 400)))


def test_elo_team_is_the_mean_of_its_members():
    """Test that a side is the mean of its members' ratings, and every member moves by the same amount."""
    model = _introduced(Elo(k=10), 'a', 'b', 'c')
    model.ratings['a'] = ELO_MEAN + 100
    assert model.game_probability(('a', 'b'), ('c',)) == pytest.approx(1 / (1 + 10 ** (-50 / 400)))
    model.update(('a', 'b'), ('c',), (0, 3), DAY)
    assert model.ratings['a'] - (ELO_MEAN + 100) == pytest.approx(model.ratings['b'] - ELO_MEAN)


def test_elo_starts_seasons_and_inheritance():
    """Test the starting offset, inheritance from a predecessor, and the pull toward the mean at a new season."""
    model = _introduced(Elo(shrink=0.25, offset=-100), 'old')
    assert model.ratings['old'] == ELO_MEAN - 100
    model.ratings['old'] = ELO_MEAN + 200
    model.introduce('new', 'old')
    model.introduce('unknown', 'missing')
    model.new_season('old')
    model.prepare(DAY)
    assert model.ratings == {'old': ELO_MEAN + 150, 'new': ELO_MEAN + 200, 'unknown': ELO_MEAN - 100}


# === Glicko-2 ===


def test_glicko2_matches_glickmans_example():
    """Test one rating period against Glickman's worked example (1500, RD 200 against three players)."""
    scale = GLICKO2_SCALE
    opponents = [((1400 - 1500) / scale, 30 / scale, 1.0), ((1550 - 1500) / scale, 100 / scale, 0.0)]
    opponents.append(((1700 - 1500) / scale, 300 / scale, 0.0))
    state = glicko2_step(GlickoState(0.0, 200 / scale, 0.06), opponents, tau=0.5)
    assert (1500 + state.mu * scale, state.phi * scale, state.sigma) == pytest.approx((1464.06, 151.52, 0.05999), 1e-4)
    # the volatility search pinned to full precision (the value other Glicko-2 implementations give)
    assert state.sigma == pytest.approx(0.059995984286488495, abs=1e-12)
    assert (state.mu, state.phi) == pytest.approx((-0.20694096667525494, 0.8721991881307343), abs=1e-12)


def test_glicko2_large_surprise():
    """Test the volatility search's other branch: a loss surprising enough that its first bracket comes from it."""
    state = glicko2_step(GlickoState(0.0, 1.5, 0.06), [(-3.0, 0.1, 0.0)], tau=0.5)
    expected = (-1.9447898675217274, 1.4300901800780255, 0.06000950336665455)
    assert (state.mu, state.phi, state.sigma) == pytest.approx(expected, abs=1e-12)


def test_glicko2_volatility_bracket_steps_down():
    """Test the volatility search when its lower bracket must step down (a huge volatility, a small tau)."""
    state = glicko2_step(GlickoState(0.0, 0.1, 100.0), [(0.0, 0.1, 1.0)], tau=3.0)
    assert 0 < state.sigma < 100
    assert state.mu > 0


def test_glicko2_model():
    """Test the starting state, the side update, inheritance and seasons."""
    model = _introduced(Glicko2(tau=0.5, start_rd=200, shrink=0.5), 'a', 'b')
    assert model.states['a'] == GlickoState(0.0, 200 / GLICKO2_SCALE, GLICKO2_VOLATILITY)
    assert model.game_probability(('a',), ('b',)) == 0.5
    model.prepare(DAY)
    model.update(('a',), ('b',), (3, 2), DAY)
    assert model.states['a'].mu == pytest.approx(-model.states['b'].mu)
    assert model.states['a'].mu > 0
    assert model.states['a'].phi < 200 / GLICKO2_SCALE
    model.introduce('c', 'a')
    model.new_season('a')
    assert model.states['c'].mu == pytest.approx(2 * model.states['a'].mu)


def test_glicko2_probability_of_sides():
    """Test that a side is its members' mean rating with their root mean square deviation, both sides' combined."""
    model = _introduced(Glicko2(), 'a', 'b', 'c', 'unknown')
    model.introduce('heir', 'missing')
    model.states |= {
        'a': GlickoState(0.4, 0.2, 0.06),
        'b': GlickoState(0.0, 0.4, 0.06),
        'c': GlickoState(0.0, 0.3, 0.06),
    }
    phi = math.sqrt((0.2**2 + 0.4**2) / 2 + 0.3**2)
    expected = 1 / (1 + math.exp(-0.2 / math.sqrt(1 + 3 * phi**2 / math.pi**2)))
    assert model.game_probability(('a', 'b'), ('c',)) == pytest.approx(expected, abs=1e-12)
    assert model.states['heir'] == model.states['unknown']


def test_glicko2_update_each_side_against_the_other():
    """Test that each side gets one rating period against the other side, its own games won as wins."""
    model = _introduced(Glicko2(tau=0.5), 'a', 'b')
    model.states |= {'a': GlickoState(0.3, 0.5, 0.06), 'b': GlickoState(-0.2, 0.8, 0.06)}
    model.update(('a',), ('b',), (3, 1), DAY)
    against_b, against_a = (-0.2, 0.8), (0.3, 0.5)
    assert model.states['a'] == glicko2_step(
        GlickoState(0.3, 0.5, 0.06), [(*against_b, 1.0)] * 3 + [(*against_b, 0.0)], 0.5
    )
    assert model.states['b'] == glicko2_step(
        GlickoState(-0.2, 0.8, 0.06), [(*against_a, 1.0)] + [(*against_a, 0.0)] * 3, 0.5
    )


def test_glicko2_team_members_use_their_side_mean():
    """Test that members of a side get the same expected scores (their side's), the less certain one moving more."""
    model = _introduced(Glicko2(), 'a', 'c')
    model.introduce('b', None)
    model.states['b'] = GlickoState(0.0, 50 / GLICKO2_SCALE, GLICKO2_VOLATILITY)
    model.update(('a', 'b'), ('c',), (3, 0), DAY)
    assert model.states['a'].mu > model.states['b'].mu > 0


# === Bradley-Terry ===


def test_bradley_terry_learns_at_the_next_day():
    """Test that strengths only change at a refit, once per day, and order the teams by results."""
    model = _introduced(BradleyTerry(half_life=180, l2=0.1), 'a', 'b', 'c')
    model.prepare(DAY)
    for _ in range(5):
        model.update(('a',), ('b',), (3, 0), DAY)
        model.update(('b',), ('c',), (3, 1), DAY)
    model.prepare(DAY + dt.timedelta(hours=1))
    assert model.game_probability(('a',), ('b',)) == 0.5  # same day: no refit
    model.prepare(DAY + dt.timedelta(days=1))
    assert model.game_probability(('a',), ('b',)) > 0.5
    assert model.game_probability(('b',), ('c',)) > 0.5
    assert model.game_probability(('a', 'b'), ('c',)) > model.game_probability(('b',), ('c',))


def test_bradley_terry_priors_and_decay():
    """Test that a new member starts at its predecessor's strength, and that old games count less."""
    model = _introduced(BradleyTerry(half_life=30, l2=1), 'a', 'b')
    model.update(('a',), ('b',), (4, 0), DAY - dt.timedelta(days=300))
    model.update(('b',), ('a',), (4, 0), DAY - dt.timedelta(days=1))
    model.new_season('a')
    model.prepare(DAY)
    assert model.game_probability(('a',), ('b',)) < 0.5  # the recent result weighs more
    model.introduce('heir', 'b')
    assert model.game_probability(('heir',), ('a',)) == model.game_probability(('b',), ('a',))


def test_bradley_terry_refit_pinned():
    """Test one refit on sides of different sizes, pinned to full precision (decay, curvature bound, steps)."""
    model = _introduced(BradleyTerry(half_life=30, l2=0.5), 'a', 'b', 'c', 'd', 'e')
    model.update(('a',), ('b',), (3, 1), DAY - dt.timedelta(days=10))
    model.update(('a', 'c', 'd'), ('b', 'e'), (2, 3), DAY - dt.timedelta(days=2))
    model.update(('c',), ('e',), (4, 0), DAY - dt.timedelta(days=1))
    model.prepare(DAY)
    model.introduce('ref', None)  # strength 0: a member's win probability against it gives its strength
    expected = [0.27603338059289934, -0.07265282547028093, 0.7953789553158411, -0.6210915241393853, -0.5724476497248013]
    gap = (expected[0] + expected[2]) / 2 - expected[1]
    assert model.game_probability(('a', 'c'), ('b',)) == pytest.approx(1 / (1 + math.exp(-gap)), abs=1e-12)
    for member, strength in zip('abcde', expected, strict=True):
        assert model.game_probability((member,), ('ref',)) == pytest.approx(1 / (1 + math.exp(-strength)), abs=1e-12)
    # a member inheriting c's strength as its prior, then losing: the next refit pulls it toward that prior
    model.introduce('heir', 'c')
    model.update(('heir',), ('b',), (1, 3), DAY)
    model.prepare(DAY + dt.timedelta(days=1))
    assert model.game_probability(('heir',), ('ref',)) == pytest.approx(1 / (1 + math.exp(0.15515637060962345)), 1e-12)


def test_bradley_terry_sides_of_any_width():
    """Test that sides of different sizes are fitted together (padding carries no weight)."""
    model = _introduced(BradleyTerry(l2=0.5), 'a', 'b', 'c', 'd', 'e')
    model.update(('a',), ('b',), (3, 0), DAY)
    model.prepare(DAY + dt.timedelta(days=1))
    model.update(('a', 'c', 'd'), ('b', 'e'), (3, 0), DAY + dt.timedelta(days=1))
    model.prepare(DAY + dt.timedelta(days=2))
    assert model.game_probability(('a',), ('b',)) > 0.5
    assert model.game_probability(('c',), ('e',)) > 0.5
