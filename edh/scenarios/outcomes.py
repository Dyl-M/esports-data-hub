"""Possible final scores of a series."""

# Local
from ..exceptions import ScenarioError
from .models import Series

Score = tuple[int, int]


def series_scores(wins_needed: int) -> tuple[Score, ...]:
    """List every final score of a series won by the first team to `wins_needed` games.

    A best-of-(2k-1) has 2k final scores, e.g. Bo5 (`wins_needed=3`): 3-0, 3-1, 3-2, 0-3, 1-3, 2-3.

    Args:
        wins_needed: Games needed to win the series.

    Returns:
        Final scores as `(team_1, team_2)`, `team_1` wins first.

    Raises:
        ValueError: If `wins_needed` is below 1.
    """
    if wins_needed < 1:
        raise ValueError(f'wins_needed must be >= 1, got {wins_needed}')
    wins = tuple((wins_needed, lost) for lost in range(wins_needed))
    losses = tuple((lost, wins_needed) for lost in range(wins_needed))
    return wins + losses


def reachable_scores(series: Series, wins_needed: int) -> tuple[Score, ...]:
    """List the final scores still reachable from the current score of a series.

    An upcoming series (0-0) can end with any score; a series at 1-1 can no longer end 3-0 or 0-3. A series already
    won (finished, or at the winning score but not flagged as finished yet) keeps its score.

    Args:
        series: The series, finished or not.
        wins_needed: Games needed to win the series.

    Returns:
        Reachable final scores as `(team_1, team_2)`.

    Raises:
        ScenarioError: If no final score is reachable (the current score is impossible for this series length).
    """
    current = (series.score_1, series.score_2)
    if series.finished or (max(current) == wins_needed and min(current) < wins_needed):
        return (current,)
    scores = tuple(
        (score_1, score_2)
        for score_1, score_2 in series_scores(wins_needed)
        if score_1 >= series.score_1 and score_2 >= series.score_2
    )
    if not scores:
        raise ScenarioError(
            f'{series.team_1} vs {series.team_2}: score {series.score_1}-{series.score_2} '
            f'is impossible in a series won at {wins_needed} games'
        )
    return scores
