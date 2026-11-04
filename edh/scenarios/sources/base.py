"""Data source interface: where league results come from (Liquipedia, CSV, ...)."""

# Standard library
from typing import Protocol

# Local
from ..models import EventInfo, LeagueConfig, Series


class ResultSource(Protocol):
    """Anything that returns the series (played, in progress and upcoming) and the event details of a league."""

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

    def fetch_event(self, config: LeagueConfig) -> EventInfo:
        """Return the event details (organizers) shown on visuals.

        Args:
            config: League format.

        Returns:
            The event details, empty when unknown.

        Raises:
            DataSourceError: If the details cannot be retrieved.
        """
        ...
