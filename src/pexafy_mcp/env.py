"""On/off settings read from the environment, one way for every module."""
from __future__ import annotations

import os

TRUE_WORDS = ("1", "true", "yes", "on")


def flag(name: str, default: bool) -> bool:
    """`default` when the variable is unset or empty; on for 1/true/yes/on; off otherwise.

    Anything unrecognised reads as off, so a typo in a switch that defaults to on
    turns the feature off rather than leaving it on unnoticed.
    """
    raw = os.environ.get(name, "")
    if raw == "":
        return default
    return raw.strip().lower() in TRUE_WORDS
