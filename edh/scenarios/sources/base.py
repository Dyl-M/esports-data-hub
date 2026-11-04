"""Data source interface: where league results come from (Liquipedia, CSV, ...)."""

# Standard library
from typing import Protocol

# Local
from ..models import LeagueConfig, Series


class ResultSource(Protocol):
    """Anything that returns the series (played, in progress and upcoming) of a league."""

    def fetch(self, config: LeagueConfig) -> list[Series]:
        """Return the league series in round order.

        Args:
            config: League format.

        Returns:
            Every series of the league, finished or not.

        Raises:
            DataSourceError: If the results cannot be retrieved.
        """
        ...
