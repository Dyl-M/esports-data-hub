"""Tests for path definitions."""

# Local
from edh import brand, paths

from .conftest import VISUALS_DIR


def test_base_dir_is_project_root():
    """BASE_DIR points at the repository root (where pyproject.toml lives)."""
    assert (paths.BASE_DIR / 'pyproject.toml').is_file()


def test_directories_are_under_base_dir():
    """Data and token directories resolve inside the project."""
    for directory in (paths.DATA_DIR, paths.TOKENS_DIR):
        assert directory.parent == paths.BASE_DIR


def test_assets_dir_is_in_the_package():
    """The brand files folder (kept out of git) resolves to edh/assets/."""
    assert paths.ASSETS_DIR == paths.BASE_DIR / 'edh' / 'assets'


def test_placeholder_assets_match_brand_file_names():
    """The test placeholders mirror the brand files named in edh.brand, so tests cover every file the card reads."""
    assets = VISUALS_DIR / 'assets'
    assert (assets / brand.AVATAR_FILE).is_file()
    assert (assets / brand.FONT_FILE).is_file()
    for icon, _ in brand.SOCIALS:
        assert (assets / 'icons' / f'{icon}.svg').is_file()


def test_tokens_json_is_in_tokens_dir():
    """The token file lives in the gitignored _tokens/ directory."""
    assert paths.TOKENS_JSON.parent == paths.TOKENS_DIR
