"""PNG export of the outlook card with Playwright's headless Chromium.

The browser is installed once with `uv run playwright install chromium`. Every format is rendered in one browser
session, at its exact pixel size.
"""

# Standard library
from datetime import date
from pathlib import Path

# Third-party
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

# Local
from ...exceptions import VisualError
from ..constants import CARD_FORMATS, CARD_PNG
from ..models import EventInfo, LeagueConfig, ScenarioReport
from .card import render_card


def export_cards(
    report: ScenarioReport, config: LeagueConfig, event: EventInfo, folder: Path, *, as_of: date
) -> list[Path]:
    """Render the outlook card to one PNG per format of `CARD_FORMATS`.

    Args:
        report: Evaluated scenarios.
        config: League format.
        event: Event details.
        folder: Target folder (created if needed); files are named `card_<format>.png`.
        as_of: Date shown on the card.

    Returns:
        The PNG paths, in `CARD_FORMATS` order.

    Raises:
        VisualError: If the card cannot be built, or the browser is missing or fails.
    """
    pages = {fmt: render_card(report, config, event, fmt=fmt, as_of=as_of) for fmt in CARD_FORMATS}
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for fmt, html in pages.items():
                    width, height = CARD_FORMATS[fmt]
                    page = browser.new_page(viewport={'width': width, 'height': height})
                    page.set_content(html, wait_until='load')
                    page.evaluate('document.fonts.ready.then(() => true)')
                    path = folder / CARD_PNG.format(format=fmt)
                    page.screenshot(path=path)
                    page.close()
                    written.append(path)
            finally:
                browser.close()
    except PlaywrightError as error:
        raise VisualError(
            f'Card rendering failed: {error.message} (install the browser with `uv run playwright install chromium`)'
        ) from error
    return written
