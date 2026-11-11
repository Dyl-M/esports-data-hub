"""Lineup identity: which team, as a core of players, plays each side of each match.

Ratings follow lineups rather than team names: renames, organization swaps and one-player changes keep the core of a
team, while a core that splits does not. The history is read in date order and both sides of a match are placed with
what was known before it, so an evaluation sees the same identities a prediction would have had. The rules and why they
are built this way: `_docs/predictions.md`, *Lineup identity*.
"""

# Standard library
import datetime as dt
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from functools import partial

# Local
from .constants import (
    CARRYOVER,
    COLLEGE_PAGE_ROOTS,
    CORE_PLAYERS,
    KIND_CLUB,
    KIND_COLLEGE,
    KIND_NATIONAL,
    SEASON_FIRST_DAYS,
    SEASON_STARTS,
)
from .models import Lineup, Lineups, Opponent, PlayedMatch, start_order

# === Public API ===


def identify_lineups(matches: Iterable[PlayedMatch]) -> Lineups:
    """Place both sides of every match in a lineup, each with only what was known before its match.

    Args:
        matches: Played matches, in any order (they are taken by start time, then id).

    Returns:
        The lineups, and the lineup ids and rosters of each match.
    """
    tracker = _Tracker()
    sides = {match.match_id: tracker.place(match) for match in sorted(matches, key=start_order)}
    return Lineups(tuple(track.lineup for track in tracker.tracks), sides, tracker.rosters)


def side_kind(match: PlayedMatch, opponent: Opponent) -> str:
    """Return the kind of team an opponent is in a match: national (flag icon), college (college event) or club.

    Args:
        match: The match.
        opponent: One of its opponents.

    Returns:
        `national`, `college` or `club`.
    """
    if opponent.national:
        return KIND_NATIONAL
    if match.page.split('/', maxsplit=1)[0] in COLLEGE_PAGE_ROOTS:
        return KIND_COLLEGE
    return KIND_CLUB


def season_of(day: dt.date) -> int:
    """Return the RLCS season a day belongs to (seasons start the day after the previous World Championship final).

    Args:
        day: The day.

    Returns:
        The season's year (e.g. 2026 for RLCS 2026); days before the first known start belong to the season before it.
    """
    index = bisect_right(SEASON_FIRST_DAYS, day)
    return SEASON_STARTS[0][0] - 1 if index == 0 else SEASON_STARTS[index - 1][0]


# === Tracking ===


@dataclass
class _Track:
    """A lineup and what the next matches are compared with: its latest roster and when it last played."""

    lineup: Lineup
    roster: frozenset[str]
    last_played: dt.datetime

    def recent(self, when: dt.datetime) -> bool:
        """Whether the lineup played within `CARRYOVER_DAYS` before when."""
        return when - self.last_played <= CARRYOVER

    def shares_core(self, roster: frozenset[str]) -> bool:
        """Whether roster keeps a core of the lineup's latest roster."""
        return len(self.roster & roster) >= CORE_PLAYERS


class _Tracker:
    """Lineups found so far, indexed by team name and by player (latest rosters)."""

    def __init__(self) -> None:
        """Start with no lineup."""
        self.tracks: list[_Track] = []
        self._by_name: dict[str, int] = {}  # latest lineup of each name
        self._rostered: dict[str, int] = {}  # latest lineup of each name that had a roster
        self._by_player: dict[str, set[int]] = {}
        self.rosters: dict[str, tuple[frozenset[str], frozenset[str]]] = {}  # roster each side played with, by match

    def place(self, match: PlayedMatch) -> tuple[int, int]:
        """Return the lineup ids of both opponents of a match, then record that they played."""
        rosters = [frozenset(player.page for player in opponent.players) for opponent in match.opponents]
        first, second = map(partial(self._lineup, match), match.opponents, rosters)
        for lineup_id, roster in ((first, rosters[0]), (second, rosters[1])):
            self._play(lineup_id, roster, match.played_at)
        self.rosters[match.match_id] = (self.tracks[first].roster, self.tracks[second].roster)
        return first, second

    def _lineup(self, match: PlayedMatch, opponent: Opponent, roster: frozenset[str]) -> int:
        """The lineup an opponent carries on, or a new one."""
        lineup_id = self._continued(opponent.name, roster, match.played_at)
        if lineup_id is None:
            lineup_id = self._start(opponent.name, side_kind(match, opponent), roster, match.played_at)
        self._by_name[opponent.name] = lineup_id
        return lineup_id

    def _continued(self, name: str, roster: frozenset[str], when: dt.datetime) -> int | None:
        """The name's lineup this side carries on; None for a new lineup."""
        lineup_id = self._by_name.get(name)
        if lineup_id is None:
            return None
        track = self.tracks[lineup_id]
        if len(roster) < CORE_PLAYERS:
            return lineup_id if track.recent(when) else None
        if not track.roster:  # a roster-less lineup, unless the roster brings the name's last core back
            earlier = self._rostered.get(name)
            return earlier if earlier is not None and self.tracks[earlier].shares_core(roster) else lineup_id
        return lineup_id if track.shares_core(roster) else None

    def _start(self, name: str, kind: str, roster: frozenset[str], when: dt.datetime) -> int:
        """Create a lineup for the name, with its predecessor."""
        lineup = Lineup(len(self.tracks), name, kind, when, self._predecessor(kind, roster, when))
        self.tracks.append(_Track(lineup, frozenset(), when))
        return lineup.lineup_id

    def _predecessor(self, kind: str, roster: frozenset[str], when: dt.datetime) -> int | None:
        """The recent lineup sharing a core with roster: club first, then most players in common, then the latest."""
        shared = Counter(lineup_id for player in roster for lineup_id in self._by_player.get(player, ()))
        cores = (self.tracks[lineup_id] for lineup_id, count in shared.items() if count >= CORE_PLAYERS)
        candidates = [
            track for track in cores if track.recent(when) and (kind != KIND_CLUB or track.lineup.kind == KIND_CLUB)
        ]
        if not candidates:
            return None
        best = max(
            candidates,
            key=lambda track: (
                track.lineup.kind == KIND_CLUB,
                shared[track.lineup.lineup_id],
                track.last_played,
                track.lineup.lineup_id,
            ),
        )
        return best.lineup.lineup_id

    def _play(self, lineup_id: int, roster: frozenset[str], when: dt.datetime) -> None:
        """Record a match of a lineup: when it played, and its latest roster when usable."""
        track = self.tracks[lineup_id]
        track.last_played = when
        if len(roster) < CORE_PLAYERS:
            return
        self._rostered[track.lineup.name] = lineup_id
        for player in track.roster - roster:
            self._by_player[player].discard(lineup_id)
        for player in roster - track.roster:
            self._by_player.setdefault(player, set()).add(lineup_id)
        track.roster = roster
