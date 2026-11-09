"""Tests for edh/scenarios/logos.py - logo downloads (mock HTTP transport, no live calls) and the local manifest."""

# Standard library
import json
import logging
from io import BytesIO

# Third-party
import httpxyz
import pytest
from PIL import Image

# Local
from edh.scenarios import logos
from edh.scenarios.constants import WATERMARK_FLOOR
from edh.scenarios.logos import TrimmedLogo, download_logos, logo_media_type, read_logos, trimmed_logo
from edh.scenarios.models import EventInfo, Logos

EVENT_URL = 'https://liquipedia.net/commons/images/7/72/Cup_icon_darkmode.png'
TEAM_URL = 'https://liquipedia.net/commons/images/thumb/4/42/Team_darkmode.png/400px-Team_darkmode.png'
EVENT = EventInfo(organizers=('NEO',), logo_url=EVENT_URL, team_logo_urls={'Gentle Mates': TEAM_URL})


class Server:
    """Mock HTTP transport serving each address's path as content, failing for the addresses in `failing`."""

    def __init__(self, failing=()):
        """Serve every address, except those in `failing` (HTTP 404); requests are recorded in `requests`."""
        self.failing = set(failing)
        self.requests = []

    def __call__(self, request):
        """Record the request, then answer it."""
        self.requests.append(request)
        if str(request.url) in self.failing:
            return httpxyz.Response(404)
        return httpxyz.Response(200, content=request.url.path.encode())

    def client(self):
        """Open an HTTP client on this transport."""
        return httpxyz.Client(transport=httpxyz.MockTransport(self))


def test_download_logos(tmp_path):
    """Test a first download: files named after the competition and the team, manifest written, logos returned."""
    server = Server()
    folder = tmp_path / 'logos'
    with server.client() as client:
        result = download_logos(EVENT, folder, client=client)
    assert result == Logos(event=folder / 'event.png', teams={'Gentle Mates': folder / 'team_gentle-mates.png'})
    assert (folder / 'event.png').read_bytes() == b'/commons/images/7/72/Cup_icon_darkmode.png'
    manifest = json.loads((folder / 'logos.json').read_text(encoding='utf-8'))
    assert manifest == {
        'event': {'url': EVENT_URL, 'file': 'event.png'},
        'teams': {'Gentle Mates': {'url': TEAM_URL, 'file': 'team_gentle-mates.png'}},
    }
    assert b'\r' not in (folder / 'logos.json').read_bytes()  # LF on every platform
    assert read_logos(folder) == result


def test_download_logos_shared_address_downloaded_once(tmp_path):
    """Test that teams sharing the game's default logo each get a file, from a single download."""
    default = 'https://liquipedia.net/commons/images/thumb/3/30/RL_default_darkmode.png/400px-RL_default_darkmode.png'
    server = Server()
    folder = tmp_path / 'logos'
    event = EventInfo(team_logo_urls={'Ikkaku': default, 'DUMORNAY GOAT': default})
    with server.client() as client:
        result = download_logos(event, folder, client=client)
    assert len(server.requests) == 1
    assert result.teams == {'Ikkaku': folder / 'team_ikkaku.png', 'DUMORNAY GOAT': folder / 'team_dumornay-goat.png'}
    assert (folder / 'team_ikkaku.png').read_bytes() == (folder / 'team_dumornay-goat.png').read_bytes()


def test_download_logos_skips_unchanged_addresses(tmp_path):
    """Test that a logo whose address did not change is not downloaded again, while a new address is."""
    folder = tmp_path / 'logos'
    with Server().client() as client:
        download_logos(EVENT, folder, client=client)
    server = Server()
    new_url = 'https://liquipedia.net/commons/images/1/12/Team_2027_darkmode.svg'
    event = EventInfo(logo_url=EVENT_URL, team_logo_urls={'Gentle Mates': new_url})
    with server.client() as client:
        result = download_logos(event, folder, client=client)
    assert [str(request.url) for request in server.requests] == [new_url]
    assert result.teams == {'Gentle Mates': folder / 'team_gentle-mates.svg'}


def test_download_logos_failure_keeps_previous_logo(tmp_path, caplog):
    """Test that a failed download is logged, keeps the previous file, and skips a logo never downloaded."""
    folder = tmp_path / 'logos'
    with Server().client() as client:
        download_logos(EventInfo(logo_url=EVENT_URL), folder, client=client)
    new_event = 'https://liquipedia.net/commons/images/0/00/Cup_2027_darkmode.png'
    server = Server(failing={new_event, TEAM_URL})
    event = EventInfo(logo_url=new_event, team_logo_urls={'Gentle Mates': TEAM_URL})
    with caplog.at_level(logging.WARNING), server.client() as client:
        result = download_logos(event, folder, client=client)
    assert result == Logos(event=folder / 'event.png')
    assert caplog.text.count('Logo download failed') == 2


def test_download_logos_first_run_failure_creates_folder(tmp_path):
    """Test that a first run whose downloads all fail still leaves a readable (empty) logos folder."""
    folder = tmp_path / 'logos'
    with Server(failing={EVENT_URL, TEAM_URL}).client() as client:
        assert download_logos(EVENT, folder, client=client) == Logos()
    assert read_logos(folder) == Logos()


def test_download_logos_without_addresses(tmp_path):
    """Test that an event without logo addresses downloads nothing and creates no folder."""
    folder = tmp_path / 'logos'
    assert download_logos(EventInfo(organizers=('NEO',)), folder) == Logos()
    assert not folder.exists()


def test_download_logos_ignores_unsupported_images(tmp_path):
    """Test that an address without an image extension is not downloaded."""
    event = EventInfo(team_logo_urls={'A': 'https://liquipedia.net/commons/images/a/aa/Logo.gif'})
    server = Server()
    with server.client() as client:
        assert download_logos(event, tmp_path / 'logos', client=client) == Logos()
    assert not server.requests


def test_download_logos_opens_its_own_client(tmp_path, monkeypatch):
    """Test that without a client, one is opened with the tool's User-Agent."""
    server = Server()
    opened = []
    real_client = httpxyz.Client

    def open_client(**kwargs):
        """Stand in for httpxyz.Client, recording its options and answering through the mock transport."""
        opened.append(kwargs)
        return real_client(transport=httpxyz.MockTransport(server), **kwargs)

    monkeypatch.setattr(logos.httpxyz, 'Client', open_client)
    download_logos(EventInfo(logo_url=EVENT_URL), tmp_path / 'logos')
    assert opened[0]['headers']['User-Agent'].startswith('esports-data-hub (https://github.com/')
    assert server.requests[0].headers['User-Agent'] == opened[0]['headers']['User-Agent']


@pytest.mark.parametrize(
    'content',
    [
        '{not json',
        '[]',
        '{"event": {"url": "u", "file": "../outside.png"}, "teams": []}',
        '{"event": {"url": "u", "file": "event.exe"}, "teams": {"A": "file.png"}}',
    ],
)
def test_read_logos_ignores_bad_manifests(tmp_path, content):
    """Test that an unreadable manifest, or entries pointing outside the folder or to non-images, give no logos."""
    (tmp_path / 'event.exe').write_bytes(b'x')
    (tmp_path / 'logos.json').write_text(content, encoding='utf-8')
    assert read_logos(tmp_path) == Logos()


def test_read_logos_skips_missing_files(tmp_path):
    """Test that a manifest entry whose file is gone is skipped, and a missing folder means no logos."""
    manifest = {'event': {'url': 'u', 'file': 'event.png'}, 'teams': {'A': {'url': 'v', 'file': 'team_a.png'}}}
    (tmp_path / 'logos.json').write_text(json.dumps(manifest), encoding='utf-8')
    (tmp_path / 'team_a.png').write_bytes(b'x')
    assert read_logos(tmp_path) == Logos(teams={'A': tmp_path / 'team_a.png'})
    assert read_logos(tmp_path / 'absent') == Logos()


@pytest.mark.parametrize(
    ('name', 'media_type'),
    [('a.png', 'image/png'), ('a.SVG', 'image/svg+xml'), ('a.jpeg', 'image/jpeg'), ('a.gif', None), ('a', None)],
)
def test_logo_media_type(tmp_path, name, media_type):
    """Test the supported logo formats."""
    assert logo_media_type(tmp_path / name) == media_type


def _two_tone(path, dark, light):
    """Write a 20x10 PNG: left half gray `dark`, right half gray `light`, with a transparent margin row."""
    image = Image.new('RGBA', (20, 12), (0, 0, 0, 0))
    image.paste((dark, dark, dark, 255), (0, 1, 10, 11))
    image.paste((light, light, light, 255), (10, 1, 20, 11))
    image.save(path)
    return path


def _trimmed(path, *, watermark=False) -> TrimmedLogo:
    """Trim a logo that is a usable image (fails the test otherwise)."""
    logo = trimmed_logo(path, watermark=watermark)
    assert logo is not None
    return logo


def _pixels(logo: TrimmedLogo):
    """Gray levels of the left and right halves of a watermark."""
    image = Image.open(BytesIO(logo.content))
    assert image.mode == 'LA'
    return image.getpixel((2, 2))[0], image.getpixel((17, 2))[0]


def test_trimmed_logo_watermark_stretches_tones(tmp_path):
    """Test that a watermark's darkest drawn tone becomes the floor gray and its lightest white."""
    logo = _trimmed(_two_tone(tmp_path / 'dark.png', 40, 200), watermark=True)
    assert _pixels(logo) == (WATERMARK_FLOOR, 255)
    assert logo.aspect == 2.0  # transparent rows trimmed


def test_trimmed_logo_watermark_single_tone_is_white(tmp_path):
    """Test that a single-tone logo becomes white, like the logos drawn in white already."""
    logo = _trimmed(_two_tone(tmp_path / 'flat.png', 60, 60), watermark=True)
    assert _pixels(logo) == (255, 255)


def test_trimmed_logo_watermark_faint_logo(tmp_path):
    """Test that a logo with only faint (semi-transparent) pixels is still measured and stretched."""
    image = Image.new('RGBA', (20, 10), (0, 0, 0, 0))
    image.paste((40, 40, 40, 100), (0, 0, 10, 10))
    image.paste((200, 200, 200, 100), (10, 0, 20, 10))
    image.save(tmp_path / 'faint.png')
    assert _pixels(_trimmed(tmp_path / 'faint.png', watermark=True)) == (WATERMARK_FLOOR, 255)


def test_trimmed_logo_keeps_colors_by_default(tmp_path):
    """Test that a logo that is not a watermark (the competition logo) keeps its colors."""
    image = Image.new('RGBA', (4, 4), (200, 30, 30, 255))
    image.save(tmp_path / 'red.png')
    logo = _trimmed(tmp_path / 'red.png')
    assert Image.open(BytesIO(logo.content)).getpixel((0, 0)) == (200, 30, 30, 255)
