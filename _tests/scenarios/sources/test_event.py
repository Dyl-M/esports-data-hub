"""Tests for edh/scenarios/sources/event.py - event details snapshot (event.json)."""

# Standard library
import json

# Third-party
import pytest

# Local
from edh.exceptions import DataSourceError
from edh.scenarios.models import EventInfo
from edh.scenarios.sources import CsvSource, read_event, write_event

from ...conftest import make_league


def test_write_then_read_round_trip(tmp_path):
    """Test that written event details read back unchanged, accents kept readable, missing folders created."""
    path = tmp_path / 'new_league' / 'snapshots' / 'event.json'
    event = EventInfo(organizers=('NEO', 'Fédération'))
    write_event(event, path)
    assert read_event(path) == event
    assert json.loads(path.read_text(encoding='utf-8')) == {'organizers': ['NEO', 'Fédération']}
    assert 'Fédération' in path.read_text(encoding='utf-8')  # written as is, not as a JSON escape
    assert b'\r' not in path.read_bytes()  # LF on every platform, like the rest of the repo


def test_read_missing_file_is_unknown(tmp_path):
    """Test that a missing snapshot means unknown details, not an error."""
    assert read_event(tmp_path / 'event.json') == EventInfo()


@pytest.mark.parametrize(
    ('content', 'message'),
    [
        ('{not json', 'Invalid JSON'),
        ('[]', 'organizers'),
        ('{"organizers": "NEO"}', 'organizers'),
        ('{"organizers": ["NEO", 1]}', 'organizers'),
    ],
)
def test_read_invalid_file(tmp_path, content, message):
    """Test that malformed snapshots are data source errors."""
    path = tmp_path / 'event.json'
    path.write_text(content, encoding='utf-8')
    with pytest.raises(DataSourceError, match=message):
        read_event(path)


def test_read_file_without_organizers(tmp_path):
    """Test that a snapshot without organizers reads as unknown organizers."""
    path = tmp_path / 'event.json'
    path.write_text('{}', encoding='utf-8')
    assert read_event(path) == EventInfo()


def test_csv_source_reads_league_snapshot(data_dir):
    """Test that the snapshot lives in the league folder and that the CSV source reads it back."""
    league = make_league()
    assert CsvSource().fetch_event(league) == EventInfo()
    write_event(EventInfo(organizers=('NEO',)), data_dir / 'test_league' / 'event.json')
    assert CsvSource(data_dir / 'other.csv').fetch_event(league).organizers == ('NEO',)
