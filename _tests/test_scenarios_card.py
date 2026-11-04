"""Tests for edh/scenarios/visuals/card.py - outlook card context and HTML (no browser)."""

# Standard library
import base64
from dataclasses import replace
from datetime import date

# Third-party
import pytest

# Local
from edh import paths
from edh.exceptions import VisualError
from edh.scenarios.engine import evaluate
from edh.scenarios.models import EvaluationOptions, EventInfo, Theme
from edh.scenarios.visuals import card
from edh.scenarios.visuals.card import card_context, primary_slot, render_card

from .conftest import make_league, make_series

pytestmark = pytest.mark.usefixtures('visual_files')

AS_OF = date(2026, 11, 4)
NEO = EventInfo(organizers=('NEO',))


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
    assert (rows['A'].badge, rows['A'].share, rows['A'].tiebreak) == ('', '83.3%', '+16.7%')
    assert (rows['B'].badge, rows['B'].share, rows['B'].undecided_width) == ('', '-', 16.67)  # only a tie can save B
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


def test_missing_template(tmp_path, monkeypatch):
    """Test that a missing card template (kept out of git) is a visual error naming the file."""
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
