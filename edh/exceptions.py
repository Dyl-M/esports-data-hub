"""Custom exceptions for the Esports Data Hub application."""


class EsportsDataHubError(Exception):
    """Base exception for all Esports Data Hub errors."""


class ConfigurationError(EsportsDataHubError):
    """Raised when there's an error with configuration or input files (missing, malformed, invalid keys)."""

    def __init__(self, message: str, *, file_path: str | None = None) -> None:
        """Initialize the error with an optional offending file path.

        Args:
            message: Human-readable description of the problem.
            file_path: Path of the file involved, if any.
        """
        super().__init__(message)
        self.file_path = file_path


class DataSourceError(EsportsDataHubError):
    """Raised when match results cannot be retrieved from a data source (Liquipedia, CSV, etc.)."""


class ScenarioError(EsportsDataHubError):
    """Raised when scenarios cannot be evaluated (no series, impossible score, unknown or oversized method)."""


class VisualError(EsportsDataHubError):
    """Raised when a visual cannot be built or rendered (no qualification slot, browser missing or failing)."""
