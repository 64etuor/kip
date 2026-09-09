"""Identify cloud placeholders without opening files or triggering hydration."""
from __future__ import annotations

from os import stat_result

# Darwin sys/stat.h: SF_DATALESS (not st_blocks, which also describes sparse files).
_SF_DATALESS = 0x40000000
# Windows winnt.h: OFFLINE, RECALL_ON_OPEN, RECALL_ON_DATA_ACCESS.
_WINDOWS_NOT_LOCAL = 0x1000 | 0x40000 | 0x400000


def is_cloud_placeholder(file_stat: stat_result) -> bool:
    """True when the OS says reading this item may require cloud retrieval."""
    return bool(
        getattr(file_stat, "st_flags", 0) & _SF_DATALESS
        or getattr(file_stat, "st_file_attributes", 0) & _WINDOWS_NOT_LOCAL
    )
