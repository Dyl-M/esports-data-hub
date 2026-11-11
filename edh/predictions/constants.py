"""Constants for rating predictions."""

# Standard library
import datetime as dt

# === History scope ===

WIKI = 'rocketleague'
# RLCS 2024 warms the ratings up, RLCS 2025 tunes them (with a season boundary inside), RLCS 2026 evaluates them
HISTORY_START = dt.date(2023, 9, 1)
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
    (2024, dt.date(2023, 8, 14)),
    (2025, dt.date(2024, 9, 16)),
    (2026, dt.date(2025, 9, 13)),
    (2027, dt.date(2026, 9, 21)),
)
SEASON_FIRST_DAYS = tuple(start for _, start in SEASON_STARTS)

# === Ratings ===

ELO_MEAN = 1500.0  # rating of an average side
ELO_SCALE = 400.0  # rating gap giving 10:1 odds per game
GLICKO2_SCALE = 173.7178  # Glicko-2 rating points per internal unit (400 / ln 10)
GLICKO2_VOLATILITY = 0.06  # starting volatility (Glickman's suggestion)
GLICKO2_TOLERANCE = 1e-6  # convergence of the volatility iteration
BT_ITERATIONS = 5  # majorize-minimize steps per Bradley-Terry refit, warm-started from the previous day's fit
SECONDS_PER_DAY = 86_400

# === Evaluation ===

TUNING_SEASON = 2025  # hyperparameters are picked on this season's predictions
EVALUATION_SEASON = 2026  # then this season is scored once
EXPERIENCED_MATCHES = 10  # a match is "experienced" when both lineups played at least this many matches before it
PROBABILITY_FLOOR = 1e-6  # predictions are clipped to [floor, 1 - floor] in log losses
CALIBRATION_BINS = 10
GROUP_ALL, GROUP_TIER = 'all', 'tier {}'  # scores per group in the report: all series, each Liquipedia tier
GROUP_EXPERIENCED, GROUP_INEXPERIENCED = 'experienced', 'inexperienced'  # both lineups with EXPERIENCED_MATCHES, or not
REPORT_DIGITS = 4  # decimals of the calibration table
IDENTITY_NAME, IDENTITY_LINEUP, IDENTITY_PLAYERS = 'name', 'lineup', 'players'
IDENTITIES = (IDENTITY_NAME, IDENTITY_LINEUP, IDENTITY_PLAYERS)
MODEL_UNIFORM, MODEL_ELO, MODEL_GLICKO2, MODEL_BRADLEY_TERRY = 'uniform', 'elo', 'glicko2', 'bradley_terry'
# Small hyperparameter grids, searched on the tuning season (an optimizer may replace them later)
ELO_GRID: dict[str, tuple[float, ...]] = {'k': (16, 24, 32, 48), 'shrink': (0, 0.25, 0.5), 'offset': (0, -50, -100)}
GLICKO2_GRID: dict[str, tuple[float, ...]] = {'tau': (0.3, 0.6, 0.9), 'start_rd': (200, 350), 'shrink': (0, 0.25, 0.5)}
BT_GRID: dict[str, tuple[float, ...]] = {'half_life': (90, 180, 365), 'l2': (0.1, 1, 10)}
PREDICTIONS_FOLDER = 'predictions'  # under `_data/`: one dated folder per evaluation
EVALUATION_JSON = 'evaluation.json'
CALIBRATION_CSV = 'calibration.csv'
