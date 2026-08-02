from __future__ import annotations

import hashlib
import re
import uuid

_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]{1,15}$")


def new_id(prefix: str) -> str:
    _validate_prefix(prefix)
    return f"{prefix}_{uuid.uuid4().hex}"


def stable_id(prefix: str, namespace: str, value: str) -> str:
    _validate_prefix(prefix)
    digest = hashlib.sha256(f"{namespace}\0{value}".encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_prefix(prefix: str) -> None:
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError(f"invalid id prefix: {prefix!r}")
