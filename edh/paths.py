"""Centralized path definitions for the project.

Resolves paths dynamically based on the script location, allowing the project to run from any directory.
"""

# Standard library
from pathlib import Path

# Base directory (project root, one level up from edh/)
BASE_DIR: Path = Path(__file__).parent.parent.resolve()

# Directory paths
DATA_DIR: Path = BASE_DIR / '_data'
TOKENS_DIR: Path = BASE_DIR / '_tokens'

# Token files
TOKENS_JSON: Path = TOKENS_DIR / 'tokens.json'
