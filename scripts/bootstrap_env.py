#!/usr/bin/env python3
"""Create a private local dotenv once; never replace deployment credentials."""
from __future__ import annotations

import os
import secrets
import sys
import tempfile
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1])
    target = root / ".env"
    if target.exists() or target.is_symlink():
        return 0
    text = (root / ".env.example").read_text(encoding="utf-8")
    for placeholder in (
        "change-me-before-use",
        "replace-with-a-long-random-secret",
        "replace-with-a-different-long-random-secret",
    ):
        text = text.replace(placeholder, secrets.token_hex(32))
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.bootstrap-", dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            return 0
    finally:
        temporary.unlink(missing_ok=True)
    print("Created private .env with random local database and API credentials.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
