"""Check local Markdown file links against the files a recipient receives."""
from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator, Mapping
from unicodedata import normalize
from urllib.parse import unquote, urlsplit

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_LINK = re.compile(r"\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+[\"'][^\n]*?[\"'])?\s*\)")


def _prose_lines(content: bytes) -> Iterator[tuple[int, str]]:
    """Numbered lines of a Markdown body outside fenced code blocks."""
    fence: str | None = None
    for number, line in enumerate(content.decode("utf-8", "replace").splitlines(), 1):
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
        yield number, line


def documentation_link_errors(files: Mapping[str, bytes], *, scope: str = "packaged") -> list[str]:
    """Report relative Markdown links whose target is not in ``files``.

    ``scope`` names the universe in each message ("packaged" for the files a
    recipient receives, "repository" for a checkout). Names and targets are
    compared in NFC, because APFS hands back NFD from a directory walk while
    git, editors and GitHub produce NFC for the same Korean filename.
    """
    errors: list[str] = []
    universe: dict[str, bytes] = {}
    for name, content in files.items():
        key = normalize("NFC", name)
        if key in universe:
            errors.append(f"{key}: two files differ only by Unicode normalization")
            continue
        universe[key] = content
    for name, content in universe.items():
        if not name.endswith(".md"):
            continue
        for number, line in _prose_lines(content):
            for match in _LINK.finditer(line):
                target = match.group(1) or match.group(2)
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                path = unquote(parsed.path)
                resolved = normalize("NFC", posixpath.normpath(
                    path.lstrip("/") if path.startswith("/")
                    else posixpath.join(posixpath.dirname(name), path)
                ))
                if resolved not in universe and not any(item.startswith(resolved + "/") for item in universe):
                    errors.append(f"{name}:{number}: {scope} link target missing: {target}")
    return errors


_PINNED = re.compile(
    r"https://github\.com/(?P<repository>[\w.-]+/[\w.-]+)/(?:blob|tree)/(?P<revision>[0-9a-f]{40})/(?P<path>[^\s)#\"'>]+)"
)


def pinned_repository_links(files: Mapping[str, bytes], repository: str) -> list[tuple[str, int, str, str]]:
    """Every commit-pinned GitHub link into ``repository``: (file, line, revision, path).

    Only a full 40-hexadecimal SHA-1 revision counts as a pin; an abbreviated
    revision is an ordinary external link and is not verified.

    The links a shipped document uses for unshipped historical records point
    at an exact revision, so they can be verified offline against the
    checkout's history instead of being skipped as external URLs. Links
    inside fenced code blocks are examples and are not collected.
    """
    found: list[tuple[str, int, str, str]] = []
    for name, content in files.items():
        if not name.endswith(".md"):
            continue
        for number, line in _prose_lines(content):
            for match in _PINNED.finditer(line):
                if match.group("repository") != repository:
                    continue
                # A bare URL at the end of a sentence carries its punctuation,
                # and a bold or italic one carries its emphasis marker.
                path = unquote(match.group("path")).rstrip(".,;:!?]*_")
                found.append((name, number, match.group("revision"), path))
    return found

