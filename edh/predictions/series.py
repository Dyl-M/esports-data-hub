"""From a per-game win probability to a series: final score distribution and series win probability.

Games of a series are taken as independent, each won by the first team with probability p. A team needing w more wins
takes the series while the other wins j more games (j below what the other needs) with probability
C(w - 1 + j, j) * p^w * (1 - p)^j: its last win ends the series, the other games come in any order.
"""

# Standard library
from math import comb

# === Public API ===


def score_distribution(p: float, wins_needed: int, current: tuple[int, int] = (0, 0)) -> dict[tuple[int, int], float]:
    """Return the probability of each final score of a series.

    Args:
        p: Probability that the first team wins a game.
        wins_needed: Games needed to win the series (3 in a best of 5).
        current: Current score, `(first, second)`.

    Returns:
        Probability of each reachable final score `(first, second)`; they sum to 1.

    Raises:
        ValueError: If `p` is outside [0, 1], or the current score is not below the winning score.
    """
    if not 0 <= p <= 1:
        raise ValueError(f'p must be between 0 and 1, got {p}')
    first, second = current
    if not (0 <= first < wins_needed and 0 <= second < wins_needed):
        raise ValueError(f'current score {current} must be below {wins_needed} for both teams')
    first_left, second_left = wins_needed - first, wins_needed - second
    scores = {(wins_needed, second + extra): _ending(p, first_left, extra) for extra in range(second_left)}
    scores |= {(first + extra, wins_needed): _ending(1 - p, second_left, extra) for extra in range(first_left)}
    return scores


def series_probability(p: float, wins_needed: int) -> float:
    """Return the probability that the first team wins a series from 0-0.

    Args:
        p: Probability that the first team wins a game.
        wins_needed: Games needed to win the series.

    Returns:
        Probability that the first team reaches `wins_needed` first.
    """
    return sum(share for (first, _), share in score_distribution(p, wins_needed).items() if first == wins_needed)


def _ending(p: float, wins_left: int, other_wins: int) -> float:
    """Chance to win the `wins_left` games needed while the other side wins `other_wins` (the last game ending it)."""
    return comb(wins_left - 1 + other_wins, other_wins) * p**wins_left * (1 - p) ** other_wins
