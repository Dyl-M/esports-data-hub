"""Constants for league play scenarios."""

# Local
from ..lpdb import LPDB_APP_NAME

# === Methods ===

METHOD_AUTO = 'auto'  # exhaustive when the scenario count allows it, then winners-first, then Monte Carlo
METHOD_EXHAUSTIVE = 'exhaustive'
METHOD_WINNERS_FIRST = 'winnersfirst'  # exact: series winners enumerated, margins only for teams tied on wins
METHOD_MONTE_CARLO = 'montecarlo'
METHODS = (METHOD_AUTO, METHOD_EXHAUSTIVE, METHOD_WINNERS_FIRST, METHOD_MONTE_CARLO)
EXACT_METHODS = frozenset({METHOD_EXHAUSTIVE, METHOD_WINNERS_FIRST})

MAX_EXHAUSTIVE_SCENARIOS = 1_000_000  # above this, 'auto' tries winners-first
# 2^15: above this, 'auto' falls back to Monte Carlo. Time is driven by large ties on wins, whose margins are still
# enumerated, so it depends on the standings: CDF Slash at 15 left (Nov 2026) took ~4 min on 16 cores, while random
# standings allowing many 6-way ties already took ~5 min single-core at 13 left
MAX_WINNER_COMBINATIONS = 32_768
DEFAULT_SAMPLES = 100_000  # Monte Carlo sample count
SAMPLE_BATCH = 10_000  # Monte Carlo samples per chunk (one numpy draw each, bounds memory)
PARALLEL_CHUNKS = 64  # exact enumerations are cut into at least this many chunks (when the league allows it)
PARALLEL_MIN_WORK = 50_000  # below this many scenarios / samples, evaluate in-process (starting workers costs ~1 s)
PARALLEL_WINNERS_COST = 200  # a winner combination weighs about this many scenarios against PARALLEL_MIN_WORK
PROJECTION_DIGITS = 12  # shares equal to this many decimals tie when projecting ranks (float sums differ per method)

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
MARGIN_FREE_TIEBREAKERS = MATCH_TIEBREAKERS | {TIEBREAKER_MANUAL}  # set by series winners only (winners-first)
TIEBREAKER_DISQUALIFIED = 'disqualified'  # always applied first by Liquipedia; no disqualifications are modelled

# === Sources ===

SOURCE_LIQUIPEDIA = 'liquipedia'
SOURCE_CSV = 'csv'
SOURCES = (SOURCE_LIQUIPEDIA, SOURCE_CSV)

LPDB_PAGE_SIZE = 100
# Logo downloads (liquipedia.net/commons): Liquipedia asks for a User-Agent naming the tool and a contact
LOGO_USER_AGENT = f'{LPDB_APP_NAME} (https://github.com/Dyl-M/esports-data-hub)'
LOGO_TIMEOUT = 20.0  # seconds per logo download
# Team logos on the card cover the same area whatever their shape; aspect ratios beyond this are clamped, so a very wide
# or very tall logo does not turn into a thin line
MAX_LOGO_ASPECT = 2.5
# Team logo watermarks are drawn in grayscale, their tones stretched from this gray (darkest drawn tone, 0-255) to
# white (lightest): a dark fill (Geekay's navy triangle) stays visible on the dark card without being bleached, and the
# logo keeps its full inner contrast
WATERMARK_FLOOR = 100
OPAQUE_ALPHA = 128  # pixels more opaque than this count as drawn when measuring a logo's tones
MEDIA_TYPE_PNG = 'image/png'
MEDIA_TYPE_SVG = 'image/svg+xml'
LOGO_MEDIA_TYPES = {
    '.png': MEDIA_TYPE_PNG,
    '.svg': MEDIA_TYPE_SVG,
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
}


# === Files ===

LEAGUE_JSON = 'league.json'  # league format, in _data/<league>/
RESULTS_CSV = 'results.csv'  # results snapshot (offline source)
EVENT_JSON = 'event.json'  # event details snapshot (organizers), rewritten by each Liquipedia run
OUTLOOK_DIR = 'outlook'  # computed per-team outlook, in _data/<league>/outlook/ (gitignored)
OUTLOOK_CSV = 'outlook.csv'
OUTLOOK_JSON = 'outlook.json'

RESULTS_COLUMNS = ('round', 'team_1', 'team_2', 'score_1', 'score_2', 'finished', 'goals', 'date')
RESULTS_OPTIONAL_COLUMNS = frozenset({'date'})  # snapshots written before the column existed still read
NO_GOALS = '-'  # `goals` cell of a series known to have no goals (a walkover); empty means unknown
LOGOS_DIR = 'logos'  # downloaded logos, in _data/<league>/logos/ (gitignored, like outlook/)
LOGOS_JSON = 'logos.json'  # logos manifest: address and file of each logo, in the logos folder
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
# Method shown in the card footer: name, short description
METHOD_LABELS = {
    METHOD_EXHAUSTIVE: ('Exhaustive', 'every combination of the remaining results'),
    METHOD_WINNERS_FIRST: ('Semi-exhaustive', 'every series winner, scores only where teams tie (exact)'),
    METHOD_MONTE_CARLO: ('Monte Carlo', 'random samples, every remaining score equally likely'),
}
DATA_CREDIT = 'Liquipedia API / LPDB v3'
GAME_NAMES = {'rocketleague': 'Rocket League'}  # Liquipedia wiki -> game name (the wiki itself otherwise)
