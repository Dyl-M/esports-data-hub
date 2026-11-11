"""Rating models: per-game win probabilities from past games.

A side of a match is a tuple of members (a team name, a lineup, or the players of a roster). Its rating is the mean of
its members' ratings, and an update moves every member. Each model is told when a member first appears (with the
member it inherits from, if any) and when a member starts a new season, then predicts and learns one series at a time.
"""

# Standard library
import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

# Third-party
import numpy as np

# Local
from .constants import (
    BT_ITERATIONS,
    ELO_MEAN,
    ELO_SCALE,
    GLICKO2_SCALE,
    GLICKO2_TOLERANCE,
    GLICKO2_VOLATILITY,
    SECONDS_PER_DAY,
)

Members = tuple[str, ...]

# === Protocol ===


class RatingModel(Protocol):
    """What the walk-forward evaluation needs from a rating model."""

    def introduce(self, member: str, predecessor: str | None) -> None:
        """Start rating a member, from its predecessor's rating when it has one."""

    def new_season(self, member: str) -> None:
        """Pull a member's rating toward the mean at its first match of a new season."""

    def prepare(self, when: dt.datetime) -> None:
        """Get ready to predict matches starting at `when` (batch models refit here)."""

    def game_probability(self, first: Members, second: Members) -> float:
        """Return the probability that the first side wins a game."""

    def update(self, first: Members, second: Members, games: tuple[int, int], when: dt.datetime) -> None:
        """Learn from a series: games won by each side."""


class _Steps:
    """The protocol's steps doing nothing, for each model to override the ones it needs."""

    def introduce(self, member: str, predecessor: str | None) -> None:
        """Nothing to set up."""

    def new_season(self, member: str) -> None:
        """Nothing to pull toward the mean."""

    def prepare(self, when: dt.datetime) -> None:
        """Nothing to fit."""

    def update(self, first: Members, second: Members, games: tuple[int, int], when: dt.datetime) -> None:
        """Nothing to learn."""


# === Uniform ===


class Uniform(_Steps):
    """The baseline: every game is a coin flip, so every score of a series is as likely as under fair games."""

    def game_probability(self, first: Members, second: Members) -> float:
        """Return 1/2, whoever plays."""
        return 0.5


# === Elo ===


@dataclass
class Elo(_Steps):
    """Elo ratings updated per game: each side moves by `k` times its games won minus its games expected.

    The defaults are only for direct use: the evaluation always passes the values tuned on its grid.

    Attributes:
        k: Rating points per game of surprise.
        shrink: Share of the gap to the mean removed at a new season.
        offset: Starting rating of a member without predecessor, relative to the mean.
    """

    k: float = 24.0
    shrink: float = 0.0
    offset: float = 0.0
    ratings: dict[str, float] = field(default_factory=dict, repr=False)

    def introduce(self, member: str, predecessor: str | None) -> None:
        """Start from the predecessor's rating, else from the mean plus `offset`."""
        if predecessor is not None and predecessor in self.ratings:
            self.ratings[member] = self.ratings[predecessor]
        else:
            self.ratings[member] = ELO_MEAN + self.offset

    def new_season(self, member: str) -> None:
        """Pull the rating toward the mean by `shrink`."""
        self.ratings[member] = ELO_MEAN + (1 - self.shrink) * (self.ratings[member] - ELO_MEAN)

    def game_probability(self, first: Members, second: Members) -> float:
        """Logistic in the rating gap: a gap of `ELO_SCALE` gives 10:1 odds."""
        gap = self._side(second) - self._side(first)
        return 1 / (1 + 10 ** (gap / ELO_SCALE))

    def update(self, first: Members, second: Members, games: tuple[int, int], when: dt.datetime) -> None:
        """Move both sides by `k` times the first side's games won minus expected."""
        won, lost = games
        delta = self.k * (won - (won + lost) * self.game_probability(first, second))
        for member in first:
            self.ratings[member] += delta
        for member in second:
            self.ratings[member] -= delta

    def _side(self, members: Members) -> float:
        """Mean rating of a side's members."""
        return sum(self.ratings[member] for member in members) / len(members)


# === Glicko-2 ===


@dataclass(frozen=True)
class GlickoState:
    """A member's Glicko-2 state, on the internal scale (rating 1500 = 0).

    Attributes:
        mu: Rating.
        phi: Rating deviation.
        sigma: Volatility.
    """

    mu: float
    phi: float
    sigma: float


def glicko2_step(
    state: GlickoState, results: Sequence[tuple[float, float, float]], tau: float, expected_mu: float | None = None
) -> GlickoState:
    """Run one Glicko-2 rating period (Glickman, *Example of the Glicko-2 system*).

    Args:
        state: The rated member before the period.
        results: Per game: opponent's rating, opponent's deviation, score (1 win, 0 loss).
        tau: System constant bounding the volatility change.
        expected_mu: Rating giving the expected scores (a team member's side mean); the member's own when None.

    Returns:
        The member after the period.
    """
    mu = state.mu if expected_mu is None else expected_mu
    terms = [_term(mu, *result) for result in results]
    variance = 1 / sum(weight**2 * e * (1 - e) for weight, e, _ in terms)
    surprise = sum(weight * (s - e) for weight, e, s in terms)
    sigma = _volatility(state, variance, variance * surprise, tau)
    phi = 1 / math.sqrt(1 / (state.phi**2 + sigma**2) + 1 / variance)
    return GlickoState(state.mu + phi**2 * surprise, phi, sigma)


def _term(mu: float, opponent: float, phi: float, score: float) -> tuple[float, float, float]:
    """A game's weight, expected score and actual score against an opponent of rating `opponent`, deviation `phi`."""
    weight = _g(phi)
    return weight, _expected(mu, opponent, weight), score


def _g(phi: float) -> float:
    """Weight of a game against an opponent of deviation `phi`."""
    return 1 / math.sqrt(1 + 3 * phi**2 / math.pi**2)


def _expected(mu: float, opponent: float, weight: float) -> float:
    """Expected score against an opponent."""
    return 1 / (1 + math.exp(-weight * (mu - opponent)))


def _volatility(state: GlickoState, variance: float, delta: float, tau: float) -> float:
    """New volatility, solved by the Illinois algorithm as in Glickman's step 5."""
    phi2, target = state.phi**2, math.log(state.sigma**2)

    def f(x: float) -> float:
        ex = math.exp(x)
        return ex * (delta**2 - phi2 - variance - ex) / (2 * (phi2 + variance + ex) ** 2) - (x - target) / tau**2

    low = target
    if delta**2 > phi2 + variance:
        high = math.log(delta**2 - phi2 - variance)
    else:
        steps = 1
        while f(target - steps * tau) < 0:
            steps += 1
        high = target - steps * tau
    f_low, f_high = f(low), f(high)
    while abs(high - low) > GLICKO2_TOLERANCE:
        middle = low + (low - high) * f_low / (f_high - f_low)
        f_middle = f(middle)
        if f_middle * f_high <= 0:
            low, f_low = high, f_high
        else:
            f_low /= 2
        high, f_high = middle, f_middle
    return math.exp(low / 2)


@dataclass
class Glicko2(_Steps):
    """Glicko-2 ratings, one rating period per series (each game an outcome against the other side).

    A side is the mean rating of its members, with the root mean square of their deviations. Each member is updated
    against the other side, its expected scores taken from its own side's mean rating. The defaults are only for direct
    use: the evaluation always passes the values tuned on its grid.

    Attributes:
        tau: System constant bounding the volatility change.
        start_rd: Deviation of a member without predecessor, in rating points.
        shrink: Share of the gap to the mean removed at a new season.
    """

    tau: float = 0.6
    start_rd: float = 350.0
    shrink: float = 0.0
    states: dict[str, GlickoState] = field(default_factory=dict, repr=False)

    def introduce(self, member: str, predecessor: str | None) -> None:
        """Start from the predecessor's state, else at the mean with deviation `start_rd`."""
        if predecessor is not None and predecessor in self.states:
            self.states[member] = self.states[predecessor]
        else:
            self.states[member] = GlickoState(0.0, self.start_rd / GLICKO2_SCALE, GLICKO2_VOLATILITY)

    def new_season(self, member: str) -> None:
        """Pull the rating toward the mean by `shrink`."""
        state = self.states[member]
        self.states[member] = GlickoState((1 - self.shrink) * state.mu, state.phi, state.sigma)

    def game_probability(self, first: Members, second: Members) -> float:
        """Expected score of the first side, both deviations combined."""
        mu_1, phi_1 = self._side(first)
        mu_2, phi_2 = self._side(second)
        return _expected(mu_1, mu_2, _g(math.sqrt(phi_1**2 + phi_2**2)))

    def update(self, first: Members, second: Members, games: tuple[int, int], when: dt.datetime) -> None:
        """Run one rating period for every member of both sides (both sides read before either changes)."""
        won, lost = games
        first_side, second_side = self._side(first), self._side(second)
        updates = self._period(first, first_side, second_side, (won, lost))
        updates |= self._period(second, second_side, first_side, (lost, won))
        self.states.update(updates)

    def _period(
        self, members: Members, side: tuple[float, float], other: tuple[float, float], games: tuple[int, int]
    ) -> dict[str, GlickoState]:
        """New states of a side's members after a series against `other`: `games` won and lost by the side."""
        wins, losses = games
        results = [(*other, 1.0)] * wins + [(*other, 0.0)] * losses
        return {member: glicko2_step(self.states[member], results, self.tau, expected_mu=side[0]) for member in members}

    def _side(self, members: Members) -> tuple[float, float]:
        """Mean rating and root mean square deviation of a side's members."""
        states = [self.states[member] for member in members]
        mu = sum(state.mu for state in states) / len(states)
        return mu, math.sqrt(sum(state.phi**2 for state in states) / len(states))


# === Bradley-Terry ===


@dataclass
class _GameLog:
    """Past series as arrays, extended at each refit with the series added since the last one.

    `entries` has one row per (series, member): series row, member index, signed weight (1 / side size for the first
    side, minus that for the second); `games` one row per series: games won by the first side, games played, time.
    """

    _pending_entries: list[tuple[int, int, float]] = field(default_factory=list)
    _pending_games: list[tuple[int, int, float]] = field(default_factory=list)
    _arrays: tuple[np.ndarray, np.ndarray] | None = None
    _count: int = 0

    def __len__(self) -> int:
        """Number of series logged."""
        return self._count

    def add(self, members: Sequence[tuple[int, float]], games: tuple[int, int], time: float) -> None:
        """Log a series: (member index, signed weight) pairs, games won by each side, start time (seconds)."""
        self._pending_entries.extend((self._count, member, sign) for member, sign in members)
        self._pending_games.append((games[0], games[0] + games[1], time))
        self._count += 1

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        """All logged series: the entries and games arrays."""
        new = (np.array(self._pending_entries, dtype=float), np.array(self._pending_games, dtype=float))
        if self._arrays is None:
            self._arrays = new
        elif self._pending_games:
            self._arrays = (np.concatenate([self._arrays[0], new[0]]), np.concatenate([self._arrays[1], new[1]]))
        self._pending_entries, self._pending_games = [], []
        return self._arrays


@dataclass
class BradleyTerry(_Steps):
    """Bradley-Terry strengths refitted on all past games before each match day.

    Maximizes the time-weighted likelihood of past games (a side's strength is the mean of its members', a game won
    with probability `sigmoid(first - second)`), with an L2 penalty pulling each member toward its prior: its
    predecessor's strength when it first appeared, else 0. Seasons need no shrinkage (`new_season` does nothing): old
    games fade with `half_life`.

    Attributes:
        half_life: Days after which a game counts half.
        l2: Strength of the pull toward the priors.
    """

    half_life: float = 180.0
    l2: float = 1.0
    _index: dict[str, int] = field(default_factory=dict, repr=False)
    _strengths: list[float] = field(default_factory=list, repr=False)
    _priors: list[float] = field(default_factory=list, repr=False)
    _log: _GameLog = field(default_factory=_GameLog, repr=False)
    _fitted_day: dt.date | None = field(default=None, repr=False)

    def introduce(self, member: str, predecessor: str | None) -> None:
        """Add a member, its prior (and starting strength) taken from its predecessor when known."""
        prior = self._strengths[self._index[predecessor]] if predecessor in self._index else 0.0
        self._index[member] = len(self._strengths)
        self._strengths.append(prior)
        self._priors.append(prior)

    def prepare(self, when: dt.datetime) -> None:
        """Refit the strengths on the games before `when`, once per day."""
        if self._fitted_day != when.date() and len(self._log):
            self._strengths = self._fit(when.timestamp()).tolist()
        self._fitted_day = when.date()

    def game_probability(self, first: Members, second: Members) -> float:
        """Sigmoid of the strength gap."""
        gap = self._side(first) - self._side(second)
        return 1 / (1 + math.exp(-gap))

    def update(self, first: Members, second: Members, games: tuple[int, int], when: dt.datetime) -> None:
        """Store the series; strengths change at the next refit."""
        members = [(self._index[member], 1 / len(first)) for member in first]
        members += [(self._index[member], -1 / len(second)) for member in second]
        self._log.add(members, games, when.timestamp())

    def _side(self, members: Members) -> float:
        """Mean strength of a side's members."""
        return sum(self._strengths[self._index[member]] for member in members) / len(members)

    def _fit(self, now: float) -> np.ndarray:
        """Majorize-minimize steps on the penalized log likelihood, from the current strengths."""
        entries, games = self._log.arrays()
        rows, members, signs = entries[:, 0].astype(np.int64), entries[:, 1].astype(np.int64), entries[:, 2]
        won, played, times = games[:, 0], games[:, 1], games[:, 2]
        strengths, priors = np.array(self._strengths), np.array(self._priors)
        size = len(strengths)
        weight = 0.5 ** ((now - times) / SECONDS_PER_DAY / self.half_life)
        # A gap is a weighted sum of strengths whose absolute weights add up to 2, so its square is at most
        # 2 * sum(|a_i| s_i^2): with a game's curvature at most n / 4, n |a_i| / 2 per member bounds the Hessian's
        # diagonal, and each step below is an ascent (majorize-minimize)
        bound = np.bincount(members, np.abs(signs) * (weight * played / 2)[rows], size) + self.l2
        for _ in range(BT_ITERATIONS):
            gap = np.bincount(rows, signs * strengths[members], len(games))
            residual = weight * (won - played / (1 + np.exp(-gap)))
            gradient = np.bincount(members, signs * residual[rows], size) - self.l2 * (strengths - priors)
            strengths += gradient / bound
        return strengths
