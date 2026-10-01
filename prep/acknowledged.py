"""Preflight FAILs the user has looked at and decided to keep for now.

Keyed by (cycle file, check name). Every preflight still prints them, tagged
with the reason, but they don't block the next stage.
"""
ACKNOWLEDGED = {}    # none in the quickstart's cycles (the full example acknowledges one delivery cycle)
