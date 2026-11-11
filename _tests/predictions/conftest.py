"""Shared helpers for the predictions tests: LPDB match records, a fake client serving them, played matches."""

# Standard library
import datetime as dt

# Local
from edh.predictions.models import Opponent, PlayedMatch, Player

TODAY = dt.date(2026, 11, 10)
PAGE = 'Rocket_League_Championship_Series/2026/Paris_Major'
PLAYED_AT = dt.datetime(2026, 5, 20, 15, tzinfo=dt.UTC)


def make_opponent(name, score=0, players='', *, national=False):
    """An opponent whose players are given as their space-separated pages."""
    return Opponent(name, name.lower(), score, tuple(Player(page, page) for page in players.split()), national)


def make_match(first, second, *, match_id='m1', played_at=PLAYED_AT, page='Some/Page'):
    """A played match of tier 1 between two opponents."""
    return PlayedMatch(match_id, page, played_at, '1', None, (first, second))


def _games(score_1, score_2):
    """Decided games giving a series score: the first team's wins, then the second's."""
    wins = [[2, 1]] * score_1 + [[0, 1]] * score_2
    return [
        {'winner': 1 if goals[0] > goals[1] else 2, 'scores': goals, 'opponents': [{'status': 'S'}, {'status': 'S'}]}
        for goals in wins
    ]


def make_record(match_id, date, score=(3, 1), **fields):
    """A minimal LPDB match record (`MATCH_FIELDS` subset) of a finished 3v3 series between Team A and Team B."""
    statuses = fields.pop('statuses', ('S', 'S'))
    names = fields.pop('names', ('Team A', 'Team B'))
    opponents = [
        {
            'name': name,
            'template': name.lower(),
            'score': goals,
            'status': status,
            'match2players': [{'name': f'{name}_p{i}', 'displayname': f'{name} {i}', 'flag': 'France'} for i in (1, 2)],
        }
        for name, goals, status in zip(names, score, statuses, strict=True)
    ]
    return {
        'match2id': match_id,
        'pagename': PAGE,
        'date': f'{date} 15:00:00',
        'dateexact': 1,
        'finished': 1,
        'mode': '3v3',
        'type': 'Offline',
        'liquipediatier': '1',
        'match2opponents': opponents,
        'match2games': _games(*score) if 'S' in statuses else [],
    } | fields


class FakeMatches:
    """Serves records as liquipydia's paginate does, then raises `error` when set."""

    def __init__(self, records, error=None):
        self.records, self.error, self.queries = records, error, []

    def paginate(self, wiki, *, conditions=None, query=None, order=None, page_size=50):
        """Yield the records, then raise the error when set."""
        self.queries.append({'wiki': wiki, 'conditions': conditions, 'query': query, 'order': order, 'size': page_size})
        yield from self.records
        if self.error:
            raise self.error


class FakeClient:
    """Stand-in for LiquipediaClient with only `/match`."""

    def __init__(self, records=(), error=None):
        self.matches = FakeMatches(list(records), error)
