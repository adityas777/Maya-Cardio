"""
losses.py — Loss record helpers (§6.1, §6.4).
"""

from __future__ import annotations
from typing import Optional


def make_loss(
    from_index: int,
    to_index: Optional[int],
    reason: str,
) -> tuple[int, Optional[int], str]:
    return (from_index, to_index, reason)


def loss_to_json(
    sensor: int,
    from_index: int,
    to_index: Optional[int],
    reason: str,
) -> dict:
    return {
        "sensor": sensor,
        "from_index": from_index,
        "to_index": to_index,
        "reason": reason,
    }


# Known reason strings (use these constants everywhere)
REASON_BUFFER_OVERFLOW = "buffer_overflow"
REASON_NOT_DELIVERED = "not_delivered"
REASON_CORRUPT_REPLY = "corrupt_reply"
REASON_CRASH_OR_POWER_LOSS = "crash_or_power_loss"
REASON_MOCK_BUFFER_EXPIRED = "mock_buffer_expired"
REASON_MOCK_RESTART = "mock_restart"
REASON_DISK_FULL = "disk_full"
REASON_SENSOR_SILENT = "sensor_silent"
