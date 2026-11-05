"""Competition and team logos, downloaded from Liquipedia into `_data/<league>/logos/` (kept local, out of git).

The folder holds the image files and a manifest, `logos.json`: `{"event": {"url": ..., "file": "event.png"},
"teams": {"Gentle Mates": {"url": ..., "file": "team_gentle-mates.png"}}}`.
A logo whose address did not change is not downloaded again. Logos are decoration: a failed download is logged and
the card is drawn without that logo, and a missing or unreadable folder means no logos.

Logo files often carry transparent margins of varying size; `trimmed_logo` crops them so logos can be sized by what is
actually drawn, and evens out the tone of watermarks (grayscale, dark logos lightened).
"""

# Standard library
import json
import logging
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Third-party
import httpxyz
from PIL import Image, ImageStat

# Local
from .config import league_dir
from .constants import (
    LOGO_MEDIA_TYPES,
    LOGO_TIMEOUT,
    LOGO_USER_AGENT,
    LOGOS_DIR,
    LOGOS_JSON,
    MEDIA_TYPE_PNG,
    MEDIA_TYPE_SVG,
    OPAQUE_ALPHA,
    WATERMARK_FLOOR,
)
from .models import EventInfo, LeagueConfig, Logos

logger = logging.getLogger(__name__)

EVENT_KEY = 'event'
TEAMS_KEY = 'teams'


def logos_dir(config: LeagueConfig) -> Path:
    """Return the league's logos folder, `_data/<league>/logos/`.

    Args:
        config: League format.

    Returns:
        The folder path (read from `paths` at call time).
    """
    return league_dir(config.slug) / LOGOS_DIR


def download_logos(event: EventInfo, folder: Path, *, client: httpxyz.Client | None = None) -> Logos:
    """Download the competition and team logos of `event` into `folder`, then rewrite its manifest.

    Args:
        event: Event details holding the logo addresses.
        folder: Logos folder (created when the event has a logo address).
        client: HTTP client (tests pass one with a mock transport); None to open one for the downloads.

    Returns:
        The logos available after the download: new ones, and previous ones whose download failed.
    """
    wanted = _wanted(event)
    if not wanted:
        return read_logos(folder)
    previous = _read_manifest(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if client is None:
        with httpxyz.Client(headers={'User-Agent': LOGO_USER_AGENT}, timeout=LOGO_TIMEOUT) as own:
            entries = _download_all(own, wanted, previous, folder)
    else:
        entries = _download_all(client, wanted, previous, folder)
    manifest: dict[str, Any] = {TEAMS_KEY: {}}
    for (kind, team), entry in entries.items():
        if kind == EVENT_KEY:
            manifest[EVENT_KEY] = entry
        else:
            manifest[TEAMS_KEY][team] = entry
    (folder / LOGOS_JSON).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return read_logos(folder)


def read_logos(folder: Path) -> Logos:
    """Read the logos listed in the manifest of `folder`, skipping files that are gone.

    Args:
        folder: Logos folder.

    Returns:
        The logos; none when the folder or its manifest is missing or unreadable.
    """
    entries = _read_manifest(folder)
    files = {key: folder / entry['file'] for key, entry in entries.items() if (folder / entry['file']).is_file()}
    return Logos(
        event=files.get((EVENT_KEY, '')),
        teams={team: path for (kind, team), path in files.items() if kind == TEAMS_KEY},
    )


def logo_media_type(path: Path) -> str | None:
    """Return the media type of a logo file from its extension, None when it is not a supported image.

    Args:
        path: Logo file.

    Returns:
        The media type (`image/png`, ...), or None.
    """
    return LOGO_MEDIA_TYPES.get(path.suffix.lower())


@dataclass(frozen=True)
class TrimmedLogo:
    """A logo ready to embed, cropped to its drawn content.

    Attributes:
        content: Image bytes.
        media_type: Media type of `content`.
        aspect: Width / height of the drawn content; 1 for a vector logo, whose content is not measured.
    """

    content: bytes
    media_type: str
    aspect: float


def trimmed_logo(path: Path, *, watermark: bool = False) -> TrimmedLogo | None:
    """Read a logo, cropping a raster image to its non-transparent pixels (re-encoded as PNG).

    Args:
        path: Logo file.
        watermark: Also turn a raster logo to grayscale, its tones stretched from `WATERMARK_FLOOR` (darkest drawn
            tone) to white (lightest). A vector logo is left as is.

    Returns:
        The logo; None when the file is missing, not a supported image, unreadable or fully transparent.
    """
    media_type = logo_media_type(path)
    if media_type is None or not path.is_file():
        return None
    if media_type == MEDIA_TYPE_SVG:
        return TrimmedLogo(path.read_bytes(), media_type, 1.0)
    try:
        with Image.open(path) as image:
            rgba = image.convert('RGBA')
    except OSError:  # also Pillow's UnidentifiedImageError (not an image), a subclass
        logger.warning('Unreadable logo %s: skipped', path)
        return None
    box = rgba.getchannel('A').getbbox()
    if box is None:
        return None
    cropped = rgba.crop(box)
    if watermark:
        cropped = _watermark_tone(cropped)
    buffer = BytesIO()
    cropped.save(buffer, format='PNG')
    return TrimmedLogo(buffer.getvalue(), MEDIA_TYPE_PNG, cropped.width / cropped.height)


def _watermark_tone(image: Image.Image) -> Image.Image:
    """Grayscale version of an RGBA image, tones of its drawn pixels stretched from `WATERMARK_FLOOR` to white.

    A single-tone logo (low == high) becomes white, like the logos drawn in white already.
    """
    gray, alpha = image.convert('L'), image.getchannel('A')
    drawn = alpha.point([255 if value > OPAQUE_ALPHA else 0 for value in range(256)])
    if drawn.getbbox() is None:  # only faint pixels: measure them all
        drawn = alpha
    low, high = ImageStat.Stat(gray, drawn).extrema[0]
    low = min(low, high - 1)  # a single tone maps to white
    span = high - low
    gray = gray.point(
        [
            round(min(max(WATERMARK_FLOOR + (value - low) * (255 - WATERMARK_FLOOR) / span, WATERMARK_FLOOR), 255))
            for value in range(256)
        ]
    )
    gray.putalpha(alpha)
    return gray


def _wanted(event: EventInfo) -> dict[tuple[str, str], str]:
    """Logo addresses keyed by `(kind, team)`: `('event', '')` for the competition, `('teams', name)` per team."""
    wanted = {(EVENT_KEY, ''): event.logo_url} if event.logo_url else {}
    wanted.update({(TEAMS_KEY, team): url for team, url in event.team_logo_urls.items()})
    return {key: url for key, url in wanted.items() if logo_media_type(Path(urlsplit(url).path))}


def _file_name(key: tuple[str, str], url: str) -> str:
    """File name of a logo: `event.<ext>`, or `team_<slug>.<ext>` from the team name."""
    suffix = Path(urlsplit(url).path).suffix.lower()
    kind, team = key
    if kind == EVENT_KEY:
        return f'event{suffix}'
    slug = re.sub(r'[^a-z0-9]+', '-', team.casefold()).strip('-') or 'team'
    return f'team_{slug}{suffix}'


def _download_all(
    client: httpxyz.Client,
    wanted: dict[tuple[str, str], str],
    previous: dict[tuple[str, str], dict[str, str]],
    folder: Path,
) -> dict[tuple[str, str], dict[str, str]]:
    """Download each wanted logo unless the previous run saved the same address; keep the old file on failure.

    An address shared by several logos (the game's default logo for teams without branding) is downloaded once.
    """
    entries = {}
    fetched: dict[str, bytes] = {}
    for key, url in wanted.items():
        old = previous.get(key)
        if old and old['url'] == url and (folder / old['file']).is_file():
            entries[key] = old
            continue
        name = _file_name(key, url)
        if url not in fetched:
            try:
                response = client.get(url, follow_redirects=True)
                response.raise_for_status()
            except httpxyz.HTTPError as error:
                logger.warning('Logo download failed for %s: %s', url, error)
                if old and (folder / old['file']).is_file():
                    entries[key] = old
                continue
            fetched[url] = response.content
        (folder / name).write_bytes(fetched[url])
        entries[key] = {'url': url, 'file': name}
    return entries


def _read_manifest(folder: Path) -> dict[tuple[str, str], dict[str, str]]:
    """Entries of the manifest keyed by `(kind, team)`; empty when it is missing or unreadable."""
    path = folder / LOGOS_JSON
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logger.warning('Unreadable logos manifest %s: logos ignored', path)
        return {}
    if not isinstance(data, dict):
        return {}
    raw: list[tuple[tuple[str, str], Any]] = [((EVENT_KEY, ''), data.get(EVENT_KEY))]
    teams = data.get(TEAMS_KEY)
    if isinstance(teams, dict):
        raw += [((TEAMS_KEY, str(team)), entry) for team, entry in teams.items()]
    return {key: entry for key, entry in raw if _is_entry(entry)}


def _is_entry(entry: object) -> bool:
    """Whether a manifest entry holds a URL and a plain file name (no folder, so it stays inside the logos folder)."""
    return (
        isinstance(entry, dict)
        and isinstance(entry.get('url'), str)
        and isinstance(entry.get('file'), str)
        and bool(entry['file'])
        and Path(entry['file']).name == entry['file']
        and logo_media_type(Path(entry['file'])) is not None
    )
