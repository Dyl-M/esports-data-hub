"""Tests for the custom exception hierarchy."""

# Third-party
import pytest

# Local
from edh.exceptions import ConfigurationError, DataSourceError, EsportsDataHubError


@pytest.mark.parametrize('error_cls', [ConfigurationError, DataSourceError])
def test_errors_derive_from_base(error_cls):
    """Every project error can be caught through the base exception."""
    with pytest.raises(EsportsDataHubError, match='boom'):
        raise error_cls('boom')


def test_configuration_error_file_path():
    """ConfigurationError keeps the offending file path, None by default."""
    assert ConfigurationError('bad').file_path is None
    assert ConfigurationError('bad', file_path='a.json').file_path == 'a.json'
