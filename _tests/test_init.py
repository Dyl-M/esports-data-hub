"""Tests for the edh package metadata."""

# Standard library
from importlib.metadata import version

# Local
import edh


def test_version_matches_project_metadata():
    """The package version is the one declared in pyproject.toml."""
    assert edh.__version__ == version('esports-data-hub')


def test_all_lists_existing_modules():
    """Every name in __all__ is an importable submodule."""
    for name in edh.__all__:
        __import__(f'edh.{name}')
