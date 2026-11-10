"""Centralized path definitions for the project.

Resolves paths dynamically based on the script location, allowing the project to run from any directory.
"""

# Standard library
from pathlib import Path

# Base directory (project root, one level up from edh/)
BASE_DIR: Path = Path(__file__).parent.parent.resolve()

# Directory paths
DATA_DIR: Path = BASE_DIR / '_data'
TOKENS_DIR: Path = BASE_DIR / '.tokens'
ASSETS_DIR: Path = BASE_DIR / 'edh' / 'assets'  # brand image, fonts and icons used by the visuals
CACHE_DIR: Path = BASE_DIR / '.cache'  # local caches of remote data, gitignored
LPDB_CACHE_DIR: Path = CACHE_DIR / 'lpdb'  # LPDB match history, one folder per wiki

# Token files ({"lpdb": "..."}, same layout as liquipedia-editing-toolkit)
TOKENS_JSON: Path = TOKENS_DIR / 'local_keys.json'
