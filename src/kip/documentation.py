"""Check local Markdown file links against the files a recipient receives."""
from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_LINK = re.compile(r"\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+[\"'][^\n]*?[\"'])?\s*\)")


def documentation_link_errors(files: Mapping[str, bytes]) -> list[str]:
    errors: list[str] = []
    for name, content in files.items():
        if not name.endswith(".md"):
            continue
        fence: str | None = None
        for number, line in enumerate(content.decode("utf-8").splitlines(), 1):
            marker = _FENCE.match(line)
            if marker:
                value = marker.group(1)
                if fence is None:
                    fence = value
                elif value[0] == fence[0] and len(value) >= len(fence):
                    fence = None
                continue
            if fence is not None:
                continue
            for match in _LINK.finditer(line):
                target = match.group(1) or match.group(2)
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                path = unquote(parsed.path)
                resolved = posixpath.normpath(
                    path.lstrip("/") if path.startswith("/")
                    else posixpath.join(posixpath.dirname(name), path)
                )
                if resolved not in files and not any(item.startswith(resolved + "/") for item in files):
                    errors.append(f"{name}:{number}: packaged link target missing: {target}")
    return errors
