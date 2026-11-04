"""Esports Data Hub (EDH) - Esports data projects, starting with league play scenarios."""

# Standard library
from importlib.metadata import version

# The version comes from the latest git tag (year.month.patch), read by hatch-vcs at install time
__version__ = version('esports-data-hub')
__author__ = 'Dylan "dyl-m" Monfret'

__all__ = [
    'exceptions',
    'paths',
    'scenarios',
]
