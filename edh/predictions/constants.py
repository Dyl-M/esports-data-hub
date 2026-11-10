"""Constants for rating predictions."""

# Standard library
import datetime as dt

# === History scope ===

WIKI = 'rocketleague'
HISTORY_START = dt.date(2024, 9, 1)  # RLCS 2025 (burn-in season), then RLCS 2026 (evaluation season)
HISTORY_TIERS = ('1', '2')  # Liquipedia tiers kept (`liquipediatier`)
TEAM_MODE = '3v3'  # match `mode` of team play; 1v1 / 2v2 events are cached but not read
REFETCH_DAYS = 30  # an update fetches again the days before the last one synced: results get edited or entered late

# === LPDB queries ===

# Fields kept per match (the `query=` allowlist); per-game and per-player details are nested in the last two
MATCH_FIELDS = (
    'match2id',
    'pagename',
    'date',
    'dateexact',
    'finished',
    'status',
    'winner',
    'walkover',
    'resulttype',
    'bestof',
    'mode',
    'type',
    'tournament',
    'parent',
    'liquipediatier',
    'liquipediatiertype',
    'extradata',
    'match2opponents',
    'match2games',
)
MATCH_ORDER = 'date ASC'
HISTORY_MAX_RETRIES = 6  # LPDB rate-limits bulk reads after a few pages: wait and retry rather than stop
HISTORY_RETRY_BACKOFF = 5.0  # seconds, doubled per retry (liquipydia caps one wait at 60 s)

# === Cache ===

PAGES_FOLDER = 'pages'  # one JSON file per tournament page
MANIFEST_FILE = 'manifest.json'  # scope and last synced day
PAGE_FILE_SUFFIX = '.json'
LAN_TYPE = 'Offline'  # match `type` of a LAN match (online ones are `Online`)
