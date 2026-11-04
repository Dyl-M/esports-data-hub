"""Opt-in tests against the live Liquipedia API (`pytest -m integration`, needs an LPDB API key)."""

# Third-party
import pytest

# Local
from edh.exceptions import ConfigurationError
from edh.scenarios.config import load_league
from edh.scenarios.engine import evaluate
from edh.scenarios.sources.liquipedia import LiquipediaSource, load_api_key


def _has_key():
    """Whether an LPDB API key is available."""
    try:
        load_api_key()
    except ConfigurationError:
        return False
    return True


pytestmark = [pytest.mark.integration, pytest.mark.skipif(not _has_key(), reason='no LPDB API key')]


def test_cdf_slash_2026_live():
    """Test that the live Coupe de France Slash 2026 League Play evaluates end to end."""
    league = load_league('cdf_slash_2026')
    series = LiquipediaSource().fetch(league)
    assert len(series) == 30
    assert len({team for item in series for team in item.teams}) == 6
    report = evaluate(league, series, method='montecarlo', samples=500, seed=0)
    assert len(report.teams) == 6
