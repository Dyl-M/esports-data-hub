"""Tests for edh/scenarios/visuals/export.py - PNG export (fake Playwright, plus one opt-in real render)."""

# Standard library
import struct
from datetime import date

# Third-party
import pytest
from playwright.sync_api import Error as PlaywrightError

# Local
from edh.exceptions import VisualError
from edh.scenarios.engine import evaluate
from edh.scenarios.models import EventInfo
from edh.scenarios.visuals import export
from edh.scenarios.visuals.export import export_cards

from ...conftest import make_league, make_series

pytestmark = pytest.mark.usefixtures('visual_files')  # placeholder template and brand files

AS_OF = date(2026, 11, 4)


def _inputs():
    """A small evaluated league and its format."""
    league = make_league(qualification={'Top': (1, 1)})
    series = [make_series(1, 'A', 'B', 3, 0), make_series(2, 'B', 'C', 3, 0), make_series(3, 'C', 'A')]
    return evaluate(league, series), league


class FakePage:
    """Records what is loaded and where the screenshot goes."""

    def __init__(self, browser, viewport):
        """Remember the viewport; register on the browser."""
        self.viewport = viewport
        self.html = None
        self.evaluated = []
        self.closed = False
        browser.pages.append(self)

    def set_content(self, html, wait_until):
        """Record the page HTML."""
        assert wait_until == 'load'
        self.html = html

    def evaluate(self, script):
        """Record scripts run in the page."""
        self.evaluated.append(script)

    @staticmethod
    def screenshot(path):
        """Write a placeholder file where the PNG would go."""
        path.write_bytes(b'png')

    def close(self):
        """Record the page closing."""
        self.closed = True


class FakeBrowser:
    """Hands out fake pages, or fails on launch / new_page."""

    def __init__(self, error=None):
        """Fail with `error` on new_page when set."""
        self.error = error
        self.pages = []
        self.closed = False

    def new_page(self, viewport):
        """Open a fake page with the given viewport."""
        if self.error:
            raise self.error
        return FakePage(self, viewport)

    def close(self):
        """Record the browser closing."""
        self.closed = True


class FakePlaywright:
    """Stand-in for `sync_playwright()`: a context manager exposing `chromium.launch()`."""

    def __init__(self, browser):
        """Launch `browser` on `chromium.launch()`."""
        self.browser = browser
        self.chromium = self

    def __call__(self):
        """Mimic `sync_playwright()` returning the context manager."""
        return self

    def __enter__(self):
        """Enter the Playwright session."""
        return self

    def __exit__(self, *exc_info):
        """Leave the Playwright session."""

    def launch(self):
        """Return the fake browser."""
        return self.browser


def test_export_cards_every_format(tmp_path, monkeypatch):
    """Test that each format is loaded at its size, waits for fonts, and lands in card_<format>.png."""
    browser = FakeBrowser()
    monkeypatch.setattr(export, 'sync_playwright', FakePlaywright(browser))
    report, league = _inputs()
    written = export_cards(report, league, EventInfo(), tmp_path / 'outlook', as_of=AS_OF)
    assert [path.name for path in written] == ['card_portrait.png', 'card_square.png', 'card_landscape.png']
    assert all(path.read_bytes() == b'png' for path in written)
    assert [page.viewport for page in browser.pages] == [
        {'width': 1080, 'height': 1350},
        {'width': 1080, 'height': 1080},
        {'width': 1200, 'height': 675},
    ]
    assert all('document.fonts.ready' in page.evaluated[0] and page.closed for page in browser.pages)
    assert 'class="frame landscape"' in browser.pages[2].html
    assert browser.closed


def test_export_cards_browser_error(tmp_path, monkeypatch):
    """Test that a Playwright failure is a visual error pointing at the browser install, browser closed."""
    browser = FakeBrowser(error=PlaywrightError('Executable does not exist'))
    monkeypatch.setattr(export, 'sync_playwright', FakePlaywright(browser))
    report, league = _inputs()
    with pytest.raises(VisualError, match='playwright install chromium'):
        export_cards(report, league, EventInfo(), tmp_path, as_of=AS_OF)
    assert browser.closed


def test_export_cards_builds_cards_before_opening_the_browser(tmp_path, monkeypatch):
    """Test that a card error (no qualification slot) is raised without starting the browser."""
    monkeypatch.setattr(export, 'sync_playwright', None)  # calling it would fail with a TypeError
    report, _ = _inputs()
    with pytest.raises(VisualError, match='no qualification slot'):
        export_cards(report, make_league(), EventInfo(), tmp_path, as_of=AS_OF)


@pytest.mark.browser
def test_export_cards_real_browser(tmp_path):
    """Test a real render with Chromium: one PNG per format, at the format's exact size."""
    report, league = _inputs()
    written = export_cards(report, league, EventInfo(organizers=('NEO',)), tmp_path, as_of=AS_OF)
    sizes = []
    for path in written:
        header = path.read_bytes()[:24]
        assert header[:8] == b'\x89PNG\r\n\x1a\n'
        sizes.append(struct.unpack('>II', header[16:24]))
    assert sizes == [(1080, 1350), (1080, 1080), (1200, 675)]
