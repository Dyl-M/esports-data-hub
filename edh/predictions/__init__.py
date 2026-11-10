"""Rating predictions: per-game win probabilities learned from past results.

Run `python -m edh.predictions.run` to fetch the LPDB match history into the local cache (`.cache/lpdb/`).
"""

__all__ = ['constants', 'history', 'models', 'run']
