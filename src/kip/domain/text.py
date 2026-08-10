from __future__ import annotations

import unicodedata


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text)
    value = value.replace("\x00", " ")
    return " ".join(value.split())
