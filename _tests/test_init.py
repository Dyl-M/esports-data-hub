"""Tests for the edh package metadata."""

# Standard library
import re
from importlib.metadata import version

# Local
import edh

# year.month.patch from a tag (unpadded month), optionally followed by a dev / local segment between tags
CALVER = re.compile(r'^\d{4}\.(?:[1-9]|1[0-2])\.\d+(?:\.dev\d+)?(?:\+[\w.]+)?$')


def test_version_matches_project_metadata():
    """The package version is the one of the installed distribution."""
    assert edh.__version__ == version('esports-data-hub')


def test_version_is_calendar_based():
    """The version derived from git tags follows year.month.patch."""
    assert CALVER.match(edh.__version__), edh.__version__


def test_all_lists_existing_modules():
    """Every name in __all__ is an importable submodule."""
    for name in edh.__all__:
        __import__(f'edh.{name}')
