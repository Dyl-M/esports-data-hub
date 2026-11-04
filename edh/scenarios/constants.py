"""Constants for league play scenarios."""

# === Methods ===

METHOD_AUTO = 'auto'  # exhaustive when the scenario count allows it, Monte Carlo otherwise
METHOD_EXHAUSTIVE = 'exhaustive'
METHOD_MONTE_CARLO = 'montecarlo'
METHODS = (METHOD_AUTO, METHOD_EXHAUSTIVE, METHOD_MONTE_CARLO)

MAX_EXHAUSTIVE_SCENARIOS = 1_000_000  # above this, 'auto' falls back to Monte Carlo
DEFAULT_SAMPLES = 100_000  # Monte Carlo sample count
SAMPLE_BATCH = 10_000  # Monte Carlo samples drawn per numpy call (bounds memory)

# === Tiebreakers ===
# Identifiers follow Liquipedia's GroupStandings `tiebreakers` parameter (Module:Standings/Tiebreaker).

CONTEXT_FULL = 'full'  # computed over every series of the league
CONTEXT_MINI_LEAGUE = 'ml'  # computed over the series played between the tied teams only
CONTEXTS = (CONTEXT_FULL, CONTEXT_MINI_LEAGUE)

TIEBREAKER_MANUAL = 'manual'  # tiebreak points set by hand by the organizer (league.json `manual_tiebreakers`)
MATCH_TIEBREAKERS = frozenset({'matchscore', 'matchdiff', 'matchwins'})
GAME_TIEBREAKERS = frozenset({'gamediff', 'gamescore', 'gamewins'})
ROUND_TIEBREAKERS = frozenset({'rounddiff', 'roundwins'})  # rounds = goals in Rocket League
TIEBREAKERS = MATCH_TIEBREAKERS | GAME_TIEBREAKERS | ROUND_TIEBREAKERS | {TIEBREAKER_MANUAL}
TIEBREAKER_DISQUALIFIED = 'disqualified'  # always applied first by Liquipedia; no disqualifications are modelled

# === Sources ===

SOURCE_LIQUIPEDIA = 'liquipedia'
SOURCE_CSV = 'csv'
SOURCES = (SOURCE_LIQUIPEDIA, SOURCE_CSV)

LPDB_API_KEY_ENV = 'LPDB_API_KEY'  # takes precedence over paths.TOKENS_JSON
LPDB_APP_NAME = 'esports-data-hub'  # User-Agent application name
LPDB_PAGE_SIZE = 100

# LPDB opponent statuses
STATUS_SCORED = 'S'  # regular (scored) result
STATUS_WALKOVER_WIN = 'W'
STATUS_WALKOVER_LOSSES = frozenset({'FF', 'DQ', 'L'})  # forfeit, disqualified, loss
STATUS_WALKOVERS = STATUS_WALKOVER_LOSSES | {STATUS_WALKOVER_WIN}

# === Files ===

LEAGUE_JSON = 'league.json'  # league format, in _data/<league>/
RESULTS_CSV = 'results.csv'  # results snapshot (offline source)
EVENT_JSON = 'event.json'  # event details snapshot (organizers), rewritten by each Liquipedia run
OUTLOOK_DIR = 'outlook'  # computed per-team outlook, in _data/<league>/outlook/ (gitignored)
OUTLOOK_CSV = 'outlook.csv'
OUTLOOK_JSON = 'outlook.json'

RESULTS_COLUMNS = ('round', 'team_1', 'team_2', 'score_1', 'score_2', 'finished', 'goals')
NO_GOALS = '-'  # `goals` cell of a series known to have no goals (a walkover); empty means unknown
CARD_PNG = 'card_{format}.png'  # outlook card per format, in _data/<league>/outlook/

# === Visuals ===

# Social media formats of the outlook card: name -> (width, height) in pixels
CARD_FORMATS: dict[str, tuple[int, int]] = {
    'portrait': (1080, 1350),  # Instagram / LinkedIn feed
    'square': (1080, 1080),
    'landscape': (1200, 675),  # X / Bluesky timeline
}
HEAT_FULL_SHARE = 0.6  # rank share drawn with the full heat color in the rank strip (lower shares fade out)
STAGE_LABEL = 'League Play'
DATA_CREDIT = 'Liquipedia API / LPDB v3'
GAME_NAMES = {'rocketleague': 'Rocket League'}  # Liquipedia wiki -> game name (the wiki itself otherwise)
