"""Outlook card: one row per team with its qualification share and the share of each final rank.

The card shows the league's first qualification slot (e.g. Playoffs); the rank strip leaves a gap after the last rank of
every slot. Brand images, icons, the font and the logos are embedded as data URIs, so the HTML renders offline. Logos
(competition, top right; teams, as watermarks behind their names) are optional: the local `logos/` folder of the league
may lack some or all of them (`EventInfo.logos`). Logos are cropped to their drawn content, and team logos are sized to
cover the same area (a wide logo is wider and flatter, a tall one narrower), so none looks smaller than the others.

The template (`templates/card.html.j2`) is public, for transparency; the brand files (`paths.ASSETS_DIR`) are kept out
of git: they exist only locally, and rendering without them raises VisualError. Tests use placeholders from
`_tests/fixtures/visuals/` (brand files, and a template printing every context value).
"""

# Standard library
import base64
import math
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import TypedDict

# Third-party
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

# Local
from ... import brand, paths
from ...exceptions import VisualError
from ..constants import (
    CARD_FORMATS,
    DATA_CREDIT,
    GAME_NAMES,
    HEAT_FULL_SHARE,
    MAX_LOGO_ASPECT,
    METHOD_LABELS,
    STAGE_LABEL,
)
from ..logos import trimmed_logo
from ..models import EventInfo, LeagueConfig, QualificationSlot, ScenarioReport, TeamOutlook, Theme
from ..report import format_share

TEMPLATES_DIR = Path(__file__).parent / 'templates'
CARD_TEMPLATE = 'card.html.j2'
STRIP_GAP = '6px'  # width of the gap marking a slot cutoff in the rank strip
LOW_SHARE = 0.05  # rank shares below this are written dimmed in the rank strip


@dataclass(frozen=True)
class StripCell:
    """One rank of a team's rank strip.

    Attributes:
        label: Share of scenarios ending at this rank, as a percentage without the sign (`60.9`, `100`, `-` for 0).
        low: Whether the share is below `LOW_SHARE` (label dimmed).
        heat: Color intensity, 0 (surface color) to 1 (full heat color).
        projected: Whether this is the team's projected rank (outlined).
        gap_after: Whether a slot cutoff follows this rank.
    """

    label: str
    low: bool
    heat: float
    projected: bool
    gap_after: bool


@dataclass(frozen=True)
class CardLogo:
    """A logo embedded in the card, sized to cover the same area as every other logo.

    Attributes:
        uri: The logo, cropped to its drawn content, as a data URI.
        width: Width as a multiple of the side of the square of the same area (the template sets that side).
        height: Height, same unit; `width * height` is 1 for every logo.
    """

    uri: str
    width: float
    height: float


@dataclass(frozen=True)
class CardRow:
    """One team row of the card.

    Attributes:
        position: Position in the projected standings.
        team: Team name.
        share: Qualification share text (`98.5%`), empty when a badge replaces it.
        tiebreak: Share of scenarios where a goal difference / manual tiebreak still settles the slot (`0.1%`), empty
            when none; part of it is already counted in `share`, ties being split evenly.
        badge: `in` / `out` when the outcome is certain (exact evaluation only), else empty.
        qualified_width: Qualified share as a bar width, in percent.
        undecided_width: Tiebreak share as a bar width, in percent.
        cells: Rank strip, rank 1 first.
        logo: Team logo, None when unknown.
    """

    position: int
    team: str
    share: str
    tiebreak: str
    badge: str
    qualified_width: float
    undecided_width: float
    cells: tuple[StripCell, ...]
    logo: CardLogo | None = None


class CardContext(TypedDict):
    """Values the card template displays (see `card_context`)."""

    format: str
    width: int
    height: int
    theme: Theme
    font_uri: str
    avatar_uri: str
    brand_name: str
    socials: list[tuple[str, str]]
    eyebrow: str
    league: str
    slot: str
    remaining: int
    scenarios: str
    scenario_unit: str
    format_rule: str
    exact: bool
    method: str
    method_note: str
    organizers: str
    event_logo: CardLogo | None
    credit: str
    ranks: int
    strip_columns: str
    rows: list[CardRow]


def render_card(report: ScenarioReport, config: LeagueConfig, event: EventInfo, *, fmt: str, as_of: date) -> str:
    """Render the outlook card as a standalone HTML page sized to the format.

    Args:
        report: Evaluated scenarios.
        config: League format (qualification slots, series length, wiki, theme).
        event: Event details (organizers, downloaded logos).
        fmt: Card format, a key of `CARD_FORMATS`.
        as_of: Date shown on the card.

    Returns:
        The HTML page.

    Raises:
        VisualError: If the format is unknown, the league has no qualification slot, or the template or a brand
            file (kept out of git) is missing.
    """
    context = card_context(report, config, event, fmt=fmt, as_of=as_of)
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True, undefined=StrictUndefined, trim_blocks=True
    )
    try:
        template = environment.get_template(CARD_TEMPLATE)
    except TemplateNotFound as error:
        raise VisualError(f'Missing card template {TEMPLATES_DIR / CARD_TEMPLATE}') from error
    return template.render(context)


def card_context(
    report: ScenarioReport, config: LeagueConfig, event: EventInfo, *, fmt: str, as_of: date
) -> CardContext:
    """Build the values the card template displays.

    Args:
        report: Evaluated scenarios.
        config: League format.
        event: Event details.
        fmt: Card format, a key of `CARD_FORMATS`.
        as_of: Date shown on the card.

    Returns:
        The template context.

    Raises:
        VisualError: If the format is unknown or the league has no qualification slot.
    """
    if fmt not in CARD_FORMATS:
        raise VisualError(f'Unknown card format {fmt!r} (formats: {", ".join(CARD_FORMATS)})')
    slot = primary_slot(report, config)
    ranks = report.rank_count
    cutoffs = {item.last for item in config.qualification if item.last < ranks}
    width, height = CARD_FORMATS[fmt]
    theme = config.theme
    return {
        'format': fmt,
        'width': width,
        'height': height,
        'theme': theme,
        'font_uri': _data_uri(paths.ASSETS_DIR / brand.FONT_FILE, 'font/ttf'),
        'avatar_uri': _data_uri(paths.ASSETS_DIR / brand.AVATAR_FILE, 'image/png'),
        'brand_name': brand.BRAND_NAME,
        'socials': [(_icon_uri(icon, theme), handle) for icon, handle in brand.SOCIALS],
        'eyebrow': f'{GAME_NAMES.get(config.wiki, config.wiki)} · {STAGE_LABEL} · {as_of.day} {as_of:%b %Y}',
        'league': report.league,
        'slot': slot.label,
        'remaining': report.remaining,
        'scenarios': f'{report.scenarios:,}',
        'scenario_unit': 'scenarios' if report.exact else 'simulations',
        'format_rule': f'Bo{config.best_of} · {_slot_range(slot)} → {slot.label}',
        'exact': report.exact,
        'method': METHOD_LABELS[report.method][0],
        'method_note': METHOD_LABELS[report.method][1],
        'organizers': ', '.join(event.organizers),
        'event_logo': card_logo(event.logos.event),
        'credit': DATA_CREDIT,
        'ranks': ranks,
        'strip_columns': _strip_columns(ranks, cutoffs),
        'rows': [
            replace(
                _row(position, outlook, slot, cutoffs, exact=report.exact),
                logo=card_logo(event.logos.teams.get(outlook.team), watermark=True),
            )
            for position, outlook in enumerate(report.teams, start=1)
        ],
    }


def primary_slot(report: ScenarioReport, config: LeagueConfig) -> QualificationSlot:
    """Return the qualification slot the card shows: the league's first one.

    Args:
        report: Evaluated scenarios.
        config: League format.

    Returns:
        The slot.

    Raises:
        VisualError: If the league has no slot, or the report does not cover it.
    """
    if not config.qualification:
        raise VisualError(f'{config.name} has no qualification slot to show on the card')
    slot = config.qualification[0]
    if slot.label not in report.slot_labels:
        raise VisualError(f'The outlook of {report.league} has no {slot.label!r} shares (re-evaluate the league)')
    return slot


def _row(position: int, outlook: TeamOutlook, slot: QualificationSlot, cutoffs: set[int], *, exact: bool) -> CardRow:
    """Build the card row of a team."""
    qualified, undecided = outlook.qualified[slot.label], outlook.undecided[slot.label]
    badge = ''
    if exact and qualified == 1:
        badge = 'in'
    elif exact and qualified == 0 and undecided == 0:
        badge = 'out'
    cells = tuple(
        StripCell(
            label=format_share(share).removesuffix('%'),  # the legend says the values are percentages
            low=share < LOW_SHARE,
            heat=round(min(share / HEAT_FULL_SHARE, 1.0), 3),
            projected=rank == outlook.projected_rank,
            gap_after=rank in cutoffs,
        )
        for rank, share in enumerate(outlook.rank_shares, start=1)
    )
    return CardRow(
        position=position,
        team=outlook.team,
        share='' if badge else format_share(outlook.slot_share[slot.label]),
        tiebreak=format_share(undecided) if undecided and not badge else '',
        badge=badge,
        qualified_width=round(qualified * 100, 2),
        undecided_width=round(undecided * 100, 2),
        cells=cells,
    )


def _slot_range(slot: QualificationSlot) -> str:
    """Describe the ranks of a slot: `Top 4`, or `#5-8` when it does not start at the top."""
    return f'Top {slot.last}' if slot.first == 1 else f'#{slot.first}-{slot.last}'


def _strip_columns(ranks: int, cutoffs: set[int]) -> str:
    """CSS grid columns of the rank strip: one track per rank, a narrow gap track after each cutoff."""
    tracks: list[str] = []
    for rank in range(1, ranks + 1):
        tracks.append('1fr')
        if rank in cutoffs:
            tracks.append(STRIP_GAP)
    return ' '.join(tracks)


def _read_asset(path: Path) -> bytes:
    """Read a brand file, raising VisualError when it is missing (the assets are kept out of git)."""
    try:
        return path.read_bytes()
    except FileNotFoundError as error:
        raise VisualError(
            f'Missing visual asset {path}: edh/assets/ is kept out of git, add the file locally'
        ) from error


def _data_uri(path: Path, media_type: str) -> str:
    """Embed a file as a base64 data URI."""
    return f'data:{media_type};base64,{base64.b64encode(_read_asset(path)).decode("ascii")}'


def _icon_uri(icon: str, theme: Theme) -> str:
    """Embed a social icon (Simple Icons SVG) as a data URI, filled with the theme's text color."""
    svg = _read_asset(paths.ASSETS_DIR / 'icons' / f'{icon}.svg').decode('utf-8')
    colored = svg.replace('<svg ', f'<svg fill="{theme.text}" ', 1)
    return f'data:image/svg+xml;base64,{base64.b64encode(colored.encode("utf-8")).decode("ascii")}'


def card_logo(path: Path | None, *, watermark: bool = False) -> CardLogo | None:
    """Embed a logo, cropped to its drawn content and sized to cover a fixed area.

    Args:
        path: Logo file; None when the logo is unknown.
        watermark: Whether the logo is a team watermark (grayscale, dark logos lightened; see `trimmed_logo`).

    Returns:
        The card logo (aspect ratio clamped to `MAX_LOGO_ASPECT`); None when there is no usable logo.
    """
    logo = trimmed_logo(path, watermark=watermark) if path else None
    if logo is None:
        return None
    aspect = min(max(logo.aspect, 1 / MAX_LOGO_ASPECT), MAX_LOGO_ASPECT)
    uri = f'data:{logo.media_type};base64,{base64.b64encode(logo.content).decode("ascii")}'
    return CardLogo(uri=uri, width=round(math.sqrt(aspect), 3), height=round(1 / math.sqrt(aspect), 3))
