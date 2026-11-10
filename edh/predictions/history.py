"""LPDB match history: fetched in bulk, cached locally, read back as played matches.

Matches are fetched by date and tier (one query for the whole scope, 1000 per page), not page by page: LPDB
rate-limits bulk reads, and a few large requests go further than hundreds of small ones. They are stored as LPDB records
(the `MATCH_FIELDS` only), grouped per tournament page in `<cache>/<wiki>/pages/`, one JSON file each, and parsed into
`PlayedMatch` on load, so a parser change needs no refetch. The manifest records the scope and the last day synced: an
update fetches again from `REFETCH_DAYS` before it (results entered or edited late), a scope change from its start.
Each page of results is saved as it arrives, so a run stopped by the rate limit keeps its progress and the next one
resumes. LPDB answers an empty page with `200 []` (liquipydia then stops) but a broken query (e.g. a field the
endpoint lacks) with 404: like any LPDB error, it stops the update with `DataSourceError`. A narrower scope leaves
earlier records in the cache: `read_history` keeps the scope's only.
"""

# Standard library
import datetime as dt
import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

# Third-party
from liquipydia import LiquipediaError, Match

# Local
from .. import paths
from ..exceptions import DataSourceError
from ..lpdb import (
    LPDB_MAX_PAGE_SIZE,
    STATUS_WALKOVERS,
    LpdbMatchClient,
    decided_goals,
    first_wins_walkover,
)
from .constants import (
    HISTORY_START,
    HISTORY_TIERS,
    LAN_TYPE,
    MANIFEST_FILE,
    MATCH_FIELDS,
    MATCH_ORDER,
    PAGE_FILE_SUFFIX,
    PAGES_FOLDER,
    REFETCH_DAYS,
    TEAM_MODE,
    WIKI,
)
from .models import Opponent, PlayedMatch, Player

logger = logging.getLogger(__name__)

# === Scope ===


@dataclass(frozen=True)
class HistoryScope:
    """Which matches the history holds.

    Attributes:
        wiki: Liquipedia wiki.
        start: First day.
        tiers: Liquipedia tiers kept.
    """

    wiki: str = WIKI
    start: dt.date = HISTORY_START
    tiers: tuple[str, ...] = HISTORY_TIERS

    def __post_init__(self) -> None:
        """Validate the scope."""
        if not self.tiers or not all(self.tiers):
            raise ValueError(f'a history scope needs tiers, none empty: {self.tiers}')

    def conditions(self, since: dt.date, until: dt.date) -> str:
        """Return the LPDB conditions selecting the scope's matches from since to until, both included.

        Args:
            since: First day.
            until: Last day.

        Returns:
            The conditions.
        """
        tiers = ' OR '.join(f'[[liquipediatier::{tier}]]' for tier in self.tiers)
        before, after = since - dt.timedelta(days=1), until + dt.timedelta(days=1)
        return f'[[date::>{before.isoformat()}]] AND [[date::<{after.isoformat()}]] AND ({tiers})'


@dataclass(frozen=True)
class HistoryUpdate:
    """What an update fetched.

    Attributes:
        since: First day fetched.
        until: Last day fetched.
        matches: Matches fetched (new or refreshed).
        pages: Tournament pages written.
    """

    since: dt.date
    until: dt.date
    matches: int
    pages: int


# === Update ===


def update_history(
    client: LpdbMatchClient,
    *,
    today: dt.date,
    scope: HistoryScope | None = None,
    refetch: bool = False,
    cache_dir: Path | None = None,
) -> HistoryUpdate:
    """Fetch the matches missing from the local history, saving each page of results as it arrives.

    Args:
        client: An open LPDB client.
        today: Last day to fetch.
        scope: Matches to hold; the default scope when None.
        refetch: Fetch everything again from the scope's start.
        cache_dir: History folder; `paths.LPDB_CACHE_DIR / <wiki>` when None.

    Returns:
        What was fetched.

    Raises:
        DataSourceError: If LPDB fails (e.g. rate limit): what arrived before is kept, run again to resume.
    """
    scope = scope or HistoryScope()
    folder = cache_dir or history_dir(scope.wiki)
    manifest = _read_manifest(folder)
    synced = None if refetch else _synced_through(manifest, scope)
    since = scope.start if synced is None else max(scope.start, synced - dt.timedelta(days=REFETCH_DAYS))
    writer = _PageWriter(folder)
    try:
        for batch in _batches(client, scope, since, today):
            writer.write(batch)
            last_day = _record_day(batch[-1])
            _write_manifest(folder, scope, max(day for day in (synced, last_day) if day is not None))
    except LiquipediaError as error:
        raise DataSourceError(
            f'LPDB history fetch stopped after {writer.matches} matches ({error}); run again to resume'
        ) from error
    _write_manifest(folder, scope, today)
    if writer.matches:
        logger.info('History %s to %s: %d matches on %d pages', since, today, writer.matches, len(writer.pages))
    else:
        logger.warning('History %s to %s: LPDB returned no match (check the scope and `MATCH_FIELDS`)', since, today)
    return HistoryUpdate(since, today, writer.matches, len(writer.pages))


def history_dir(wiki: str = WIKI) -> Path:
    """Return the history folder of a wiki, under `paths.LPDB_CACHE_DIR` (read at call time)."""
    return paths.LPDB_CACHE_DIR / wiki


def _batches(
    client: LpdbMatchClient, scope: HistoryScope, since: dt.date, until: dt.date
) -> Iterator[list[dict[str, Any]]]:
    """Yield the scope's matches from since to until in date order, one LPDB page at a time."""
    records = client.matches.paginate(
        scope.wiki,
        conditions=scope.conditions(since, until),
        query=','.join(MATCH_FIELDS),
        order=MATCH_ORDER,
        page_size=LPDB_MAX_PAGE_SIZE,
    )
    batch: list[dict[str, Any]] = []
    try:
        for record in records:
            batch.append(record)
            if len(batch) == LPDB_MAX_PAGE_SIZE:
                yield batch
                batch = []
    except LiquipediaError:
        if batch:
            yield batch  # keep what arrived before the error
        raise
    if batch:
        yield batch


def _record_day(record: dict[str, Any]) -> dt.date:
    """UTC day of an LPDB match record (`date` is `YYYY-MM-DD HH:MM:SS`)."""
    return dt.date.fromisoformat(str(record['date'])[:10])


@dataclass
class _PageWriter:
    """Merges fetched records into the page files, keyed by `match2id` (a refetched match replaces the cached one)."""

    folder: Path
    matches: int = 0
    pages: set[str] = field(default_factory=set)

    def write(self, records: Sequence[dict[str, Any]]) -> None:
        """Merge records into the files of their pages."""
        by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            by_page[str(record['pagename'])].append(record)
        fetched_at = dt.datetime.now(dt.UTC).isoformat(timespec='seconds')
        for page, page_records in by_page.items():
            path = page_file(self.folder, page)
            merged = {str(record['match2id']): record for record in _read_page(path)}
            merged |= {str(record['match2id']): record for record in page_records}
            ordered = sorted(merged.values(), key=_record_order)
            _write_json(path, {'fetched_at': fetched_at, 'matches': ordered})
        self.matches += len(records)
        self.pages.update(by_page)


def _record_order(record: dict[str, Any]) -> tuple[str, str]:
    """Sort key of a cached record: date, then id."""
    return str(record['date']), str(record['match2id'])


def page_file(folder: Path, page: str) -> Path:
    """Return the cache file of a tournament page (its name, percent-encoded so `/` is safe)."""
    return folder / PAGES_FOLDER / f'{quote(page, safe="")}{PAGE_FILE_SUFFIX}'


def _read_page(path: Path) -> list[dict[str, Any]]:
    """Records of a page file; none when the file does not exist yet."""
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding='utf-8'))['matches'])


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write data as JSON through a temporary file, so an interrupted run never leaves a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8', newline='\n')
    temporary.replace(path)


def _read_manifest(folder: Path) -> dict[str, Any]:
    """The manifest of a history folder; empty when there is none yet."""
    path = folder / MANIFEST_FILE
    if not path.exists():
        return {}
    data: dict[str, Any] = json.loads(path.read_text(encoding='utf-8'))
    return data


def _synced_through(manifest: dict[str, Any], scope: HistoryScope) -> dt.date | None:
    """Last day synced, when the manifest is for the same scope (None otherwise: fetch from the scope's start)."""
    same_scope = manifest.get('start') == scope.start.isoformat() and tuple(manifest.get('tiers', ())) == scope.tiers
    synced = manifest.get('synced_through')
    return dt.date.fromisoformat(synced) if same_scope and synced else None


def _write_manifest(folder: Path, scope: HistoryScope, synced_through: dt.date) -> None:
    """Record the scope and the last day synced."""
    _write_json(
        folder / MANIFEST_FILE,
        {
            'wiki': scope.wiki,
            'start': scope.start.isoformat(),
            'tiers': list(scope.tiers),
            'synced_through': synced_through.isoformat(),
            'updated_at': dt.datetime.now(dt.UTC).isoformat(timespec='seconds'),
        },
    )


# === Read ===


def read_history(
    scope: HistoryScope | None = None, *, cache_dir: Path | None = None, mode: str = TEAM_MODE
) -> list[PlayedMatch]:
    """Return the finished matches of the local history, oldest first.

    Args:
        scope: Matches kept (tiers, from the start day); the default scope when None.
        cache_dir: History folder; `paths.LPDB_CACHE_DIR / <wiki>` when None.
        mode: Match mode kept (team play by default).

    Returns:
        Every finished match of the scope and mode with two named teams and a winner, sorted by start time then id.
    """
    scope = scope or HistoryScope()
    folder = cache_dir or history_dir(scope.wiki)
    records = (
        record for path in sorted((folder / PAGES_FOLDER).glob(f'*{PAGE_FILE_SUFFIX}')) for record in _read_page(path)
    )
    played = (match for match in _played(records, mode) if match is not None)
    matches = [match for match in played if match.tier in scope.tiers and match.played_at.date() >= scope.start]
    return sorted(matches, key=lambda match: (match.played_at, match.match_id))


def _played(records: Iterable[dict[str, Any]], mode: str) -> Iterator[PlayedMatch | None]:
    """Parse the records of the given mode."""
    for record in records:
        if record.get('mode') == mode:
            yield to_played_match(Match.model_validate(record))


def to_played_match(match: Match) -> PlayedMatch | None:
    """Convert an LPDB match into a played match.

    Args:
        match: The LPDB match.

    Returns:
        The played match; None when it is not finished, lacks a date or two named teams, or has no winner (a tie,
        or a series not entered yet).
    """
    opponents = _named_opponents(match)
    if not match.finished or match.date is None or opponents is None:
        return None
    scores, walkover = _scores(opponents)
    if scores[0] == scores[1]:
        logger.debug('Skipped match %s: %s-%s', match.match2id, *scores)
        return None
    first, second = (_opponent(opponent, score) for opponent, score in zip(opponents, scores, strict=True))
    return PlayedMatch(
        match_id=str(match.match2id),
        page=str(match.pagename),
        played_at=match.date.replace(tzinfo=dt.UTC),
        tier=str(match.liquipediatier or ''),
        lan=_is_lan(match.type),
        opponents=(first, second),
        games=() if walkover else decided_goals(match),
        walkover=walkover,
    )


def _named_opponents(match: Match) -> list[dict[str, Any]] | None:
    """The two opponents of a match when both are named and differ; None otherwise (e.g. TBD)."""
    opponents = list(match.match2opponents or [])
    names = [opponent.get('name') for opponent in opponents]
    if len(names) != 2 or not all(names) or names[0] == names[1]:
        return None
    return opponents


def _scores(opponents: Sequence[dict[str, Any]]) -> tuple[list[int], bool]:
    """Series score of the two opponents and whether it was a walkover (then 1 for the winner, 0 for the other)."""
    statuses = [str(opponent.get('status') or '') for opponent in opponents]
    if any(status in STATUS_WALKOVERS for status in statuses):
        return ([1, 0] if first_wins_walkover(statuses) else [0, 1]), True
    return [max(int(opponent.get('score') or 0), 0) for opponent in opponents], False


def _opponent(opponent: dict[str, Any], score: int) -> Opponent:
    """A team of a match, with its roster."""
    players = tuple(
        Player(str(player['name']), str(player.get('displayname') or player['name']), str(player.get('flag') or ''))
        for player in opponent.get('match2players') or []
        if isinstance(player, dict) and player.get('name')
    )
    return Opponent(str(opponent['name']), str(opponent.get('template') or ''), score, players)


def _is_lan(match_type: str | None) -> bool | None:
    """Whether a match `type` is LAN (`Offline`), online, or unknown."""
    if not match_type:
        return None
    return match_type == LAN_TYPE
