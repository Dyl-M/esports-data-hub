"""Tests for edh/scenarios/visuals/card.py - outlook card context and HTML (no browser)."""

# Standard library
import base64
from dataclasses import replace
from datetime import date
from io import BytesIO

# Third-party
import pytest
from PIL import Image

# Local
from edh import paths
from edh.exceptions import VisualError
from edh.scenarios.engine import evaluate
from edh.scenarios.models import EvaluationOptions, EventInfo, Logos, Theme
from edh.scenarios.visuals import card
from edh.scenarios.visuals.card import CardLogo, card_context, card_logo, primary_slot, render_card

from ...conftest import make_league, make_series

pytestmark = pytest.mark.usefixtures('visual_files')

AS_OF = date(2026, 11, 4)
NEO = EventInfo(organizers=('NEO',))
REAL_TEMPLATES_DIR = card.TEMPLATES_DIR  # read before the visual_files fixture swaps in the placeholder


def _league(**kwargs):
    """A three-team league whose first slot is the top spot, then the top two."""
    return make_league(qualification=kwargs.pop('qualification', {'Top': (1, 1), 'Top 2': (1, 2)}), **kwargs)


def _report(league, series=None, method='exhaustive'):
    """Evaluate the league with one series left (C vs A) unless other series are given."""
    series = series or [make_series(1, 'A', 'B', 3, 0), make_series(2, 'B', 'C', 3, 0), make_series(3, 'C', 'A')]
    return evaluate(league, series, EvaluationOptions(method=method, samples=300, seed=1))


def _context(league=None, report=None, event=NEO, fmt='portrait'):
    """Build the card context of the default league and report."""
    league = league or _league()
    return card_context(report or _report(league), league, event, fmt=fmt, as_of=AS_OF)


def test_context_header():
    """Test the header values: game, stage and date, slot, scenarios, format rule."""
    context = _context()
    assert context['eyebrow'] == 'Rocket League · League Play · 4 Nov 2026'
    assert (context['league'], context['slot'], context['remaining']) == ('Test League', 'Top', 1)
    assert (context['scenarios'], context['scenario_unit'], context['exact']) == ('6', 'scenarios', True)
    assert (context['method'], context['method_note']) == ('Exhaustive', 'every combination of the remaining results')
    assert context['format_rule'] == 'Bo5 · Top 1 → Top'
    assert (context['width'], context['height'], context['organizers']) == (1080, 1350, 'NEO')


def test_context_rows_follow_projected_standings():
    """Test the rows: projected order, positions, strip cells with the projected rank outlined."""
    rows = _context()['rows']
    assert [(row.position, row.team) for row in rows] == [(1, 'A'), (2, 'B'), (3, 'C')]
    first = rows[0]
    assert len(first.cells) == 3
    assert [cell.projected for cell in first.cells] == [True, False, False]
    assert first.cells[0].heat == 1.0  # 89% of scenarios, above the full heat share
    assert [(cell.label, cell.low) for cell in first.cells] == [('88.9', False), ('5.6', False), ('5.6', False)]


def test_context_exact_badges_and_tiebreak_share():
    """Test that an exact evaluation shows IN / OUT badges and the share left to goal difference."""
    rows = {row.team: row for row in _context()['rows']}
    # the share splits the three-way tie of the 3-0 scenario evenly, like the #1 cell of the rank strip
    assert (rows['A'].badge, rows['A'].share, rows['A'].tiebreak) == ('', '88.9%', '16.7%')
    assert rows['A'].share == f'{rows["A"].cells[0].label}%'
    assert (rows['B'].badge, rows['B'].share, rows['B'].undecided_width) == ('', '5.6%', 16.67)  # only a tie can save B
    series = [make_series(1, 'A', 'B', 3, 0), make_series(2, 'A', 'C', 3, 0), make_series(3, 'B', 'C')]
    rows = {row.team: row for row in _context(report=_report(_league(), series))['rows']}
    assert (rows['A'].badge, rows['A'].share, rows['A'].qualified_width) == ('in', '', 100.0)
    assert (rows['B'].badge, rows['B'].tiebreak) == ('out', '')
    assert [(cell.label, cell.low) for cell in rows['A'].cells] == [('100', False), ('-', True), ('-', True)]


def test_context_monte_carlo_never_claims_certainty():
    """Test that Monte Carlo cards say simulations and never show badges."""
    league = _league()
    series = [make_series(1, 'A', 'B', 3, 0), make_series(2, 'A', 'C', 3, 0), make_series(3, 'B', 'C')]
    context = _context(league, _report(league, series, method='montecarlo'))
    assert (context['scenario_unit'], context['exact'], context['method']) == ('simulations', False, 'Monte Carlo')
    assert not any(row.badge for row in context['rows'])
    assert context['rows'][0].share == '100%'
    assert context['rows'][0].tiebreak == ''  # nothing left to a tie: no "+0%"


def test_context_strip_gaps_after_each_cutoff():
    """Test that the rank strip leaves a gap after every slot's last rank, except the last rank of the league."""
    context = _context(_league(qualification={'Top': (1, 1), 'Top 2': (1, 2), 'All': (1, 3)}))
    assert context['strip_columns'] == '1fr 6px 1fr 6px 1fr'
    assert [cell.gap_after for cell in context['rows'][0].cells] == [True, True, False]


def test_context_slot_not_starting_at_the_top():
    """Test the format rule of a slot covering middle ranks."""
    league = _league(qualification={'Play-in': (2, 3)})
    assert _context(league)['format_rule'] == 'Bo5 · #2-3 → Play-in'


def test_context_unknown_wiki_is_shown_as_is():
    """Test that a wiki missing from the game names is displayed as its identifier."""
    league = replace(_league(), wiki='valorant')
    assert _context(league)['eyebrow'].startswith('valorant · ')


@pytest.mark.parametrize(
    ('fmt', 'size'), [('portrait', (1080, 1350)), ('square', (1080, 1080)), ('landscape', (1200, 675))]
)
def test_render_card_formats(fmt, size):
    """Test that each format renders through the template with its size, every team, the brand and the data credit."""
    html = render_card(_report(_league()), _league(), NEO, fmt=fmt, as_of=AS_OF)
    assert f'class="frame {fmt}" style="width: {size[0]}px; height: {size[1]}px' in html
    for text in ('Test League | Top |', 'organizers=NEO | credit=Liquipedia API / LPDB v3', 'u/Dyl_M'):
        assert text in html
    assert 'data:font/ttf;base64,' in html
    assert 'data:image/png;base64,' in html
    assert 'data:image/svg+xml;base64,' in html
    assert html.count('class="row"') == 3


def test_render_card_applies_theme_and_escapes_names():
    """Test that the league theme reaches the template and that team names are HTML-escaped."""
    league = replace(_league(), theme=Theme(background='#061533', glow='#0443c861'))
    series = [make_series(1, '<A&Co>', 'B', 3, 0), make_series(2, 'B', 'C', 3, 0), make_series(3, 'C', '<A&Co>')]
    html = render_card(_report(league, series), league, NEO, fmt='portrait', as_of=AS_OF)
    assert '--c-bg: #061533;' in html
    assert '--c-glow: #0443c861;' in html
    assert '&lt;A&amp;Co&gt;' in html
    assert '<A&Co>' not in html


def test_context_icons_take_the_theme_text_color():
    """Test that the social icons are filled with the theme's text color."""
    league = replace(_league(), theme=Theme(text='#f0f4fa'))
    icon_uri, handle = _context(league)['socials'][0]
    assert handle == 'Dyl-M'
    assert b'<svg fill="#f0f4fa" ' in base64.b64decode(icon_uri.removeprefix('data:image/svg+xml;base64,'))


@pytest.mark.parametrize('fmt', ['portrait', 'square', 'landscape'])
def test_real_template_renders(monkeypatch, fmt):
    """Test that the real card template renders every context value it uses (strict: a missing one fails)."""
    monkeypatch.setattr(card, 'TEMPLATES_DIR', REAL_TEMPLATES_DIR)
    html = render_card(_report(_league()), _league(), NEO, fmt=fmt, as_of=AS_OF)
    assert f'<div class="frame {fmt}">' in html
    assert 'Test League' in html
    assert 'Method: Exhaustive' in html


def test_missing_template(tmp_path, monkeypatch):
    """Test that a missing card template is a visual error naming the file."""
    monkeypatch.setattr(card, 'TEMPLATES_DIR', tmp_path)
    with pytest.raises(VisualError, match=r'Missing card template .*card\.html\.j2'):
        render_card(_report(_league()), _league(), NEO, fmt='square', as_of=AS_OF)


def test_missing_asset(tmp_path, monkeypatch):
    """Test that a missing brand file (kept out of git) is a visual error naming the file."""
    monkeypatch.setattr(paths, 'ASSETS_DIR', tmp_path)
    with pytest.raises(VisualError, match=r'Missing visual asset .*JetBrainsMono'):
        _context()


def test_unknown_format():
    """Test that an unknown format is a visual error naming the formats."""
    with pytest.raises(VisualError, match='portrait, square, landscape'):
        _context(fmt='story')


def test_primary_slot_errors():
    """Test that a league without slots, or a report without the slot's shares, cannot make a card."""
    with pytest.raises(VisualError, match='no qualification slot'):
        primary_slot(_report(_league()), make_league())
    with pytest.raises(VisualError, match="no 'Playoffs' shares"):
        primary_slot(_report(_league()), _league(qualification={'Playoffs': (1, 2)}))


def test_context_names_the_semi_exhaustive_method():
    """Test that a winners-first report is labelled semi-exhaustive, and still exact."""
    context = _context(report=_report(_league(), method='winnersfirst'))
    assert (context['method'], context['exact'], context['scenario_unit']) == ('Semi-exhaustive', True, 'scenarios')


def _png(path, size, box=None):
    """Write a transparent PNG of `size`, opaque inside `box` (the whole image by default)."""
    width, height = size
    image = Image.new('RGBA', size, (0, 0, 0, 0))
    image.paste((255, 255, 255, 255), box or (0, 0, width, height))
    image.save(path)
    return path


def _logo(path) -> CardLogo:
    """Build the card logo of an image that has one (fails the test otherwise)."""
    logo = card_logo(path)
    assert logo is not None
    return logo


def _decoded(logo: CardLogo):
    """Open the image embedded in a card logo."""
    return Image.open(BytesIO(base64.b64decode(logo.uri.removeprefix('data:image/png;base64,'))))


def test_context_embeds_downloaded_logos(tmp_path):
    """Test that the competition logo and each team's logo are embedded; teams without one get none."""
    (tmp_path / 'team_a.svg').write_bytes(b'<svg/>')
    event_logo = _png(tmp_path / 'event.png', (40, 40))
    logos = Logos(event=event_logo, teams={'A': tmp_path / 'team_a.svg', 'B': tmp_path / 'gone.png'})
    context = _context(event=replace(NEO, logos=logos))
    assert context['event_logo'] is not None
    assert context['event_logo'].uri.startswith('data:image/png;base64,')
    rows = {row.team: row.logo for row in context['rows']}
    assert rows['A'] == CardLogo(f'data:image/svg+xml;base64,{base64.b64encode(b"<svg/>").decode()}', 1.0, 1.0)
    assert rows['B'] is rows['C'] is None  # B's file is gone, C has no logo


def test_context_watermarks_team_logos_only(tmp_path):
    """Test that team logos are embedded as grayscale watermarks, while the competition logo keeps its colors."""
    image = Image.new('RGBA', (20, 20), (200, 30, 30, 255))
    image.save(tmp_path / 'red.png')
    logos = Logos(event=tmp_path / 'red.png', teams={'A': tmp_path / 'red.png'})
    context = _context(event=replace(NEO, logos=logos))
    event_logo = context['event_logo']
    team_logo = {row.team: row.logo for row in context['rows']}['A']
    assert event_logo is not None
    assert team_logo is not None
    assert _decoded(event_logo).getpixel((0, 0)) == (200, 30, 30, 255)
    assert _decoded(team_logo).mode == 'LA'


def test_card_logo_trims_margins_and_keeps_the_same_area(tmp_path):
    """Test that logos are cropped to their drawn content and sized to the same area, whatever their shape."""
    wide = _logo(_png(tmp_path / 'wide.png', (100, 100), box=(10, 40, 90, 60)))  # 80x20 drawn, 4:1 → clamped
    square = _logo(_png(tmp_path / 'square.png', (100, 100), box=(30, 30, 70, 70)))
    tall = _logo(_png(tmp_path / 'tall.png', (90, 200)))
    thin = _logo(_png(tmp_path / 'thin.png', (20, 200)))  # 1:10 → clamped
    assert _decoded(wide).size == (80, 20)
    assert _decoded(square).size == (40, 40)
    assert (square.width, square.height) == (1.0, 1.0)
    assert (wide.width, wide.height) == (1.581, 0.632)  # aspect clamped to 2.5
    assert (tall.width, tall.height) == (0.671, 1.491)  # aspect 0.45
    assert (thin.width, thin.height) == (0.632, 1.581)  # aspect clamped to 1 / 2.5
    for logo in (wide, square, tall, thin):
        assert logo.width * logo.height == pytest.approx(1, abs=0.002)


def test_card_logo_without_usable_image(tmp_path):
    """Test that a missing, fully transparent or unreadable logo is left out."""
    (tmp_path / 'broken.png').write_bytes(b'not a png')
    assert card_logo(None) is None
    assert card_logo(tmp_path / 'gone.png') is None
    assert card_logo(_png(tmp_path / 'empty.png', (10, 10), box=(0, 0, 0, 0))) is None
    assert card_logo(tmp_path / 'broken.png') is None


def test_render_card_with_and_without_logos(tmp_path):
    """Test that the template receives the logos and their sizes, and that a card without logos has no logo image."""
    logo = _png(tmp_path / 'event.png', (40, 40))
    event = replace(NEO, logos=Logos(event=logo, teams={'A': logo}))
    html = render_card(_report(_league()), _league(), event, fmt='square', as_of=AS_OF)
    assert html.count('class="event-logo"') == 1
    assert html.count('class="team-logo"') == 1
    assert 'data-size="1.0x1.0"' in html
    html = render_card(_report(_league()), _league(), NEO, fmt='square', as_of=AS_OF)
    assert 'logo"' not in html
