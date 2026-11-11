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

# === Match parsing ===

LAN_TYPE = 'Offline'  # match `type` of a LAN match (online ones are `Online`)
NATIONAL_ICON_PREFIX = 'Flag of '  # national teams take a flag as their team icon (`Flag of France 4 by 3.png`)

# === Lineup identity ===

CORE_PLAYERS = 2  # players kept together for a roster to carry a lineup on (2 of 3 in Rocket League)
CARRYOVER_DAYS = 120  # a new lineup inherits only from a lineup that played this recently
CARRYOVER = dt.timedelta(days=CARRYOVER_DAYS)
COLLEGE_PAGE_ROOTS = ('Collegiate_Rocket_League',)  # first part of the pages of college events
KIND_CLUB, KIND_NATIONAL, KIND_COLLEGE = 'club', 'national', 'college'
TEAM_KINDS = (KIND_CLUB, KIND_NATIONAL, KIND_COLLEGE)
# First day of each RLCS season: the day after the previous World Championship final, so off-season events (where new
# rosters form) count in the coming season; days before the first one belong to the season before it
SEASON_STARTS = (
    (2025, dt.date(2024, 9, 16)),
    (2026, dt.date(2025, 9, 13)),
    (2027, dt.date(2026, 9, 21)),
)
SEASON_FIRST_DAYS = tuple(start for _, start in SEASON_STARTS)
