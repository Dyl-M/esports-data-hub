"""Tests for path definitions."""

# Local
from edh import paths


def test_base_dir_is_project_root():
    """BASE_DIR points at the repository root (where pyproject.toml lives)."""
    assert (paths.BASE_DIR / 'pyproject.toml').is_file()


def test_directories_are_under_base_dir():
    """Data and token directories resolve inside the project."""
    for directory in (paths.DATA_DIR, paths.TOKENS_DIR):
        assert directory.parent == paths.BASE_DIR


def test_tokens_json_is_in_tokens_dir():
    """The token file lives in the gitignored _tokens/ directory."""
    assert paths.TOKENS_JSON.parent == paths.TOKENS_DIR
