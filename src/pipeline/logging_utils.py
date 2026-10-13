"""
logging_utils.py — Centralised logging setup.
"""

from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        stream=sys.stderr,
        level=numeric,
        format="%(asctime)s %(name)-20s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
