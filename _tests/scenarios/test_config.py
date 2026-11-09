"""Tests for edh/scenarios/config.py - league format loading and validation."""

# Standard library
import json
import re

# Third-party
import pytest

# Local
from edh.exceptions import ConfigurationError
from edh.scenarios.config import league_dir, load_league, parse_league
from edh.scenarios.models import Theme

VALID = {
    'name': 'Test League',
    'wiki': 'rocketleague',
    'pagename': 'Test/League',
    'best_of': 5,
    'tiebreakers': ['matchscore', 'gamediff', 'ml.gamediff'],
    'qualification': {'Playoffs': [1, 4]},
    'walkover_games': 3,
    'team_aliases': {'Old': 'New'},
    'manual_tiebreakers': {'A': 1},
}


def _write(data_dir, content):
    """Write a league.json in the temporary data folder."""
    folder = data_dir / 'test_league'
    folder.mkdir()
    (folder / 'league.json').write_text(content, encoding='utf-8')


def test_load_league(data_dir):
    """Test that a valid league file is fully parsed."""
    _write(data_dir, json.dumps(VALID))
    league = load_league('test_league')
    assert (league.slug, league.name, league.best_of, league.walkover_games) == ('test_league', 'Test League', 5, 3)
    assert [str(tiebreaker) for tiebreaker in league.tiebreakers] == VALID['tiebreakers']
    assert [(slot.label, slot.first, slot.last) for slot in league.qualification] == [('Playoffs', 1, 4)]
    assert league.team_aliases == {'Old': 'New'}
    assert league.manual_tiebreakers == {'A': 1}


def test_league_dir_reads_paths_at_call_time(data_dir):
    """Test that the league folder follows paths.DATA_DIR."""
    assert league_dir('x') == data_dir / 'x'


def test_load_league_missing_file(data_dir):
    """Test that a missing league file is a configuration error naming the file."""
    with pytest.raises(ConfigurationError, match='not found') as error:
        load_league('absent')
    assert error.value.file_path == str(data_dir / 'absent' / 'league.json')


def test_load_league_invalid_json(data_dir):
    """Test that malformed JSON is a configuration error."""
    _write(data_dir, '{not json')
    with pytest.raises(ConfigurationError, match='Invalid JSON'):
        load_league('test_league')


def test_parse_league_minimal():
    """Test that optional keys default to empty values, and the theme to the neutral palette."""
    required = {key: VALID[key] for key in ('name', 'wiki', 'pagename', 'best_of', 'tiebreakers')}
    league = parse_league('x', required)
    assert (league.qualification, league.walkover_games, league.team_aliases) == ((), 0, {})
    assert league.theme == Theme()


def test_parse_league_partial_theme():
    """Test that a theme overrides the colors it gives and keeps the neutral value of the others."""
    league = parse_league('x', VALID | {'theme': {'background': '#061533', 'glow': '#0443c861'}})
    assert (league.theme.background, league.theme.glow) == ('#061533', '#0443c861')
    assert league.theme.text == Theme().text


@pytest.mark.parametrize(
    ('data', 'message'),
    [
        ([], 'JSON object'),
        ({'name': 'x'}, 'Missing keys'),
        (VALID | {'best_of': 4}, 'best_of'),
        (VALID | {'best_of': True}, 'best_of must be an integer'),
        (VALID | {'tiebreakers': ['buchholz']}, 'Unsupported tiebreaker'),
        (VALID | {'qualification': {'Playoffs': [4, 1]}}, 'rank range'),
        (VALID | {'qualification': {'Playoffs': [1]}}, 'Invalid league file'),
        (VALID | {'manual_tiebreakers': {'A': 'high'}}, 'must be an integer'),
        (VALID | {'name': 123}, 'name must be a string'),
        (VALID | {'tiebreakers': 'matchscore'}, 'tiebreakers must be a list'),
        (VALID | {'tiebreakers': ['matchscore', 1]}, 'tiebreakers must be a string'),
        (VALID | {'qualification': {'Playoffs': [1, 2, 3]}}, r'\[first, last\] rank range'),
        (VALID | {'qualification': {'Playoffs': 4}}, 'Playoffs must be a list'),
        (VALID | {'qualification': []}, 'qualification must be an object'),
        (VALID | {'team_aliases': {'Old': 1}}, 'Old must be a string'),
        (VALID | {'manual_tiebreakers': ['A']}, 'manual_tiebreakers must be an object'),
        (VALID | {'theme': ['#000000']}, 'theme must be an object'),
        (VALID | {'theme': {'backdrop': '#000000'}}, 'unknown theme colors: backdrop'),
        (VALID | {'theme': {'text': 'white'}}, 'theme color text must be #rrggbb'),
        (VALID | {'theme': {'text': 255}}, 'theme.text must be a string'),
    ],
)
def test_parse_league_invalid(data, message):
    """Test that invalid league files are configuration errors."""
    with pytest.raises(ConfigurationError, match=message):
        parse_league('x', data, file_path='league.json')


@pytest.mark.parametrize(
    ('field', 'value', 'described'),
    [
        ('best_of', True, 'best_of must be an integer, got a boolean (true / false)'),
        ('best_of', 2.5, 'best_of must be an integer, got a decimal number'),
        ('best_of', '5', 'best_of must be an integer, got a string'),
        ('best_of', None, 'best_of must be an integer, got null'),
        ('name', 5, 'name must be a string, got an integer number'),
        ('name', ['x'], 'name must be a string, got a list (JSON array)'),
        ('name', {'x': 1}, 'name must be a string, got an object (JSON key / value mapping)'),
    ],
)
def test_parse_league_names_json_types(field, value, described):
    """Test that type errors name the JSON type received rather than echoing the raw value."""
    with pytest.raises(ConfigurationError, match=re.escape(described)):
        parse_league('x', VALID | {field: value})


def test_cdf_slash_2026_league_file():
    """Test that the committed Coupe de France Slash 2026 format loads."""
    league = load_league('cdf_slash_2026')
    assert league.best_of == 5
    assert [str(tiebreaker) for tiebreaker in league.tiebreakers] == [
        'matchscore',
        'gamediff',
        'ml.gamediff',
        'ml.rounddiff',
        'manual',
    ]
    assert league.theme.background == '#061533'  # from the event banner


@pytest.mark.parametrize(
    ('region', 'spots'),
    [('eu', 4), ('na', 4), ('mena', 2), ('sam', 2), ('apac', 1), ('oce', 1), ('ssa', 1)],
)
def test_rlcs_2027_league_files(region, spots):
    """Test that the committed RLCS 2027 Club League formats load with the rulebook's format and Finale spots."""
    league = load_league(f'rlcs_2027_{region}')
    assert league.wiki == 'rocketleague'
    assert league.pagename.startswith('Rocket_League_Championship_Series/Club_Championship/2027/Club_League/')
    assert league.best_of == 5
    assert [str(tiebreaker) for tiebreaker in league.tiebreakers] == ['matchscore', 'gamediff', 'rounddiff', 'manual']
    assert [(slot.label, slot.first, slot.last) for slot in league.qualification] == [('Club Finale', 1, spots)]
    assert league.walkover_games == 3
