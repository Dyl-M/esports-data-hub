"""Tests for edh/predictions/evaluation.py - walk-forward evaluation, scores and tuning."""

# Standard library
import datetime as dt
import math
from dataclasses import replace

# Third-party
import pytest

# Local
from edh.exceptions import DataSourceError
from edh.predictions import evaluation
from edh.predictions.evaluation import (
    Prediction,
    breakdown,
    calibration,
    evaluate_all,
    first_team_share,
    score,
    tune,
    walk_forward,
)
from edh.predictions.lineups import identify_lineups
from edh.predictions.ratings import Elo, Uniform

from .conftest import make_match, make_opponent

START = dt.datetime(2025, 5, 1, 18, tzinfo=dt.UTC)  # RLCS 2025


class _Spy(Uniform):
    """A uniform model recording every call, in order."""

    def __init__(self):
        self.calls = []

    def introduce(self, member, predecessor):
        self.calls.append(('introduce', member, predecessor))

    def new_season(self, member):
        self.calls.append(('new_season', member))

    def game_probability(self, first, second):
        self.calls.append(('predict', first, second))
        return 0.5

    def update(self, first, second, games, when):
        self.calls.append(('update', first, second, games))


def _match(day, first, second, games=(3, 1), **kwargs):
    """A match on day (from START) between two (name, players) sides."""
    (first_name, first_players), (second_name, second_players) = first, second
    opponents = (
        make_opponent(first_name, games[0], first_players),
        make_opponent(second_name, games[1], second_players),
    )
    return make_match(*opponents, match_id=f'm{day:03d}', played_at=START + dt.timedelta(days=day), **kwargs)


def _walk(matches, identity='name', model=None):
    """Run the walk-forward with a model (a spy by default); return the model and the predictions."""
    model = model or _Spy()
    return model, walk_forward(matches, identify_lineups(matches), identity, model)


A, B, C = ('A', 'a1 a2 a3'), ('B', 'b1 b2 b3'), ('C', 'c1 c2 c3')

# === Walk-forward ===


def test_each_match_predicted_before_it_is_learned():
    """Test that the members are introduced, then the match predicted, then learned from."""
    model, predictions = _walk([_match(0, A, B)])
    assert model.calls == [
        ('introduce', 'A', None),
        ('introduce', 'B', None),
        ('predict', ('A',), ('B',)),
        ('update', ('A',), ('B',), (3, 1)),
    ]
    assert predictions == [Prediction('m000', 2025, '1', False, 0.5, 0.5, (3, 1))]


def test_walkovers_are_neither_predicted_nor_learned():
    """Test that a walkover adds its teams but no prediction or update."""
    walkover = replace(make_match(make_opponent('A', 1), make_opponent('B', 0), played_at=START), walkover=True)
    model, predictions = _walk([walkover])
    assert [call[0] for call in model.calls] == ['introduce', 'introduce']
    assert predictions == []


@pytest.mark.parametrize(
    ('identity', 'members'),
    [('name', ('A',)), ('lineup', ('0',)), ('players', ('a1', 'a2', 'a3'))],
)
def test_members_per_identity(identity, members):
    """Test that a side is rated as its name, its lineup id, or its players."""
    model, _ = _walk([_match(0, A, B)], identity)
    assert model.calls[-2][1] == members


def test_players_without_roster():
    """Test that a side without roster uses its lineup's latest roster, or its name when it never had one."""
    matches = [_match(0, A, B), _match(1, ('A', ''), ('D', ''))]
    model, _ = _walk(matches, 'players')
    assert model.calls[-2][1:] == (('a1', 'a2', 'a3'), ('D',))


def test_lineups_inherit_and_seasons_start():
    """Test that a renamed lineup is introduced from its predecessor and a new season is announced once per member."""
    matches = [_match(0, A, B), _match(140, ('A', 'a1 a2 a3'), B), _match(150, ('Rebrand', 'a1 a2 x'), B)]
    model, _ = _walk(matches, 'lineup')
    assert ('new_season', '0') in model.calls  # May 2025, then September 2025 (RLCS 2026)
    assert ('new_season', '1') in model.calls
    assert ('introduce', '2', '0') in model.calls
    assert ('new_season', '2') not in model.calls  # its predecessor already played this season


def test_heir_of_an_earlier_season_starts_the_new_season():
    """Test that a lineup inheriting from one last seen in an earlier season is pulled toward the mean like it."""
    matches = [_match(0, A, B), _match(110, A, B), _match(140, ('Rebrand', 'a1 a2 x'), B)]  # A in 2025, Rebrand in 2026
    model, _ = _walk(matches, 'lineup')
    start = model.calls.index(('introduce', '2', '0'))
    calls = model.calls[start:]
    assert calls[:2] == [('introduce', '2', '0'), ('new_season', '2')]


def test_series_probability_uses_the_winning_score():
    """Test that the series probability is computed for the series' length (the winner's score)."""
    _, predictions = _walk([_match(0, A, B, (4, 2)), _match(1, A, B, (2, 0))], model=Elo(k=100))
    first, second = predictions
    assert first.series_probability == 0.5
    assert second.series_probability == pytest.approx(second.game_probability**2 * (3 - 2 * second.game_probability))


def test_experience_counts_matches_before():
    """Test that a match is experienced once both lineups have played `EXPERIENCED_MATCHES` matches."""
    matches = [_match(day, A, B) for day in range(11)]
    _, predictions = _walk(matches)
    assert [prediction.experienced for prediction in predictions] == [False] * 10 + [True]


# === Scores ===


def _prediction(p, games=(3, 1), **kwargs):
    """A prediction with game probability p (the series probability too, unless given)."""
    fields = {'match_id': 'm', 'season': 2026, 'tier': '1', 'experienced': True, 'game_probability': p}
    return Prediction(**({**fields, 'series_probability': p, 'games': games} | kwargs))


def test_uniform_scores():
    """Test that coin flips score ln 2 per series and per game, a Brier score of 1/4 and half the series right."""
    scores = score([_prediction(0.5), _prediction(0.5, (1, 3))])
    assert (scores.matches, scores.brier, scores.accuracy) == (2, 0.25, 0.5)
    assert scores.log_loss == pytest.approx(math.log(2))
    assert scores.game_log_loss == pytest.approx(math.log(2))


def test_scores_reward_confident_right_predictions():
    """Test the scores of one right and one wrong prediction, certainty clipped so log losses stay finite."""
    scores = score([_prediction(0.8, (3, 0)), _prediction(1.0, (0, 3))])
    assert scores.accuracy == 0.5
    assert scores.brier == pytest.approx((0.2**2 + 1) / 2)
    assert scores.log_loss == pytest.approx((-math.log(0.8) - math.log(1e-6)) / 2)
    assert scores.game_log_loss == pytest.approx((-3 * math.log(0.8) - 3 * math.log(1e-6)) / 6)


@pytest.mark.parametrize(
    ('p', 'games', 'correct'), [(0.6, (3, 1), 1.0), (0.6, (1, 3), 0.0), (0.4, (1, 3), 1.0), (0.5, (1, 3), 0.5)]
)
def test_accuracy_counts_favorites(p, games, correct):
    """Test that a series counts as right when its winner was the favorite, half when it was a coin flip."""
    assert score([_prediction(p, games)]).accuracy == correct


def test_score_needs_predictions():
    """Test that scoring nothing is an error."""
    with pytest.raises(ValueError, match='no prediction'):
        score([])


def test_calibration_bins():
    """Test that predictions are grouped by predicted probability, the last bin including 1."""
    predictions = [_prediction(0.05, (0, 3)), _prediction(0.15, (3, 0)), _prediction(0.95), _prediction(1.0)]
    bins = calibration(predictions, bins=10)
    assert [(row.low, row.matches) for row in bins] == [(0.0, 1), (0.1, 1), (0.9, 2)]
    assert (bins[2].predicted, bins[2].observed) == (pytest.approx(0.975), 1.0)


def test_breakdown_groups():
    """Test the groups: all, each tier, experienced and inexperienced, empty groups left out."""
    groups = breakdown([_prediction(0.6), _prediction(0.6, tier='2', experienced=False)])
    assert {name: scores.matches for name, scores in groups.items()} == {
        'all': 2,
        'tier 1': 1,
        'tier 2': 1,
        'experienced': 1,
        'inexperienced': 1,
    }


# === Tuning ===


def test_tune_keeps_the_best_tuning_log_loss(monkeypatch):
    """Test that tuning keeps the grid point with the lowest tuning-season log loss, scored on the next season."""
    monkeypatch.setitem(evaluation.MODELS, 'elo', (Elo, {'k': (0, 60)}))
    matches = [_match(day, A, B) for day in range(0, 200, 10)]  # A always wins: learning helps
    result = tune(matches, identify_lineups(matches), 'elo', 'name')
    assert (result.model, result.identity, result.params) == ('elo', 'name', {'k': 60})
    assert result.tuning.matches == len([day for day in range(0, 200, 10) if day < 138])
    assert {prediction.season for prediction in result.predictions} == {2026}


def test_evaluate_all_order(monkeypatch):
    """Test that every model is tuned for every identity, models first."""
    monkeypatch.setattr(evaluation, 'tune', lambda _matches, _lineups, model, identity: (model, identity))
    matches = [_match(0, A, B), _match(200, A, B)]  # one played match in each season
    assert evaluate_all(matches, identify_lineups(matches), ['uniform', 'elo'], ['name', 'players']) == [
        ('uniform', 'name'),
        ('uniform', 'players'),
        ('elo', 'name'),
        ('elo', 'players'),
    ]


@pytest.mark.parametrize(
    ('days', 'missing'), [((0,), 'RLCS 2026'), ((200,), 'RLCS 2025'), ((), 'RLCS 2025, RLCS 2026')]
)
def test_evaluate_all_needs_both_seasons(days, missing):
    """Test that a history without a played match in the tuning or evaluation season is a data error."""
    matches = [_match(day, A, B) for day in days]
    matches.append(replace(_match(201, A, C), walkover=True))  # walkovers don't count
    with pytest.raises(DataSourceError, match=f'No played match of {missing}'):
        evaluate_all(matches, identify_lineups(matches))


def test_first_team_share():
    """Test the share of series won by the team listed first."""
    assert first_team_share([_prediction(0.5), _prediction(0.5, (1, 3)), _prediction(0.5, (3, 2))]) == 2 / 3
