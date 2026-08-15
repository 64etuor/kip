"""Shared zip-bomb guard for every OOXML/zip-container parser.

DOCX, XLSX, and PPTX are all ZIP containers, and a crafted archive can store
a handful of kilobytes on disk that expand to gigabytes in memory (a
"decompression bomb"): a single highly-compressible member, deeply nested
archives, or an archive with enough entries to exhaust memory just from
central-directory bookkeeping. ``check_zip_bomb_guard`` inspects
``ZipFile.infolist()`` metadata only - it never decompresses a member - so
the check itself stays cheap regardless of how large the declared
uncompressed size is, and callers can reject a hostile archive before any
member is ever read into memory.

Every zip-backed parser (xlsx shallow parse, xlsx deep range read, docx,
pptx package scan) must call this before reading any archive member.
"""

from __future__ import annotations

import zipfile

from kip.errors import ValidationError

_DEFAULT_MAX_ENTRIES = 100_000
_DEFAULT_MAX_UNCOMPRESSED = 2_147_483_648
_DEFAULT_MAX_RATIO = 200


def check_zip_bomb_guard(
    archive: zipfile.ZipFile,
    *,
    format_name: str,
    max_entries: int = _DEFAULT_MAX_ENTRIES,
    max_uncompressed: int = _DEFAULT_MAX_UNCOMPRESSED,
    max_ratio: int = _DEFAULT_MAX_RATIO,
) -> None:
    """Reject an archive whose declared size makes it a decompression bomb.

    ``format_name`` (e.g. ``"XLSX"``, ``"DOCX"``, ``"PPTX"``) is only used to
    make the raised :class:`ValidationError` identify which parser rejected
    the archive; the limits themselves are format-agnostic.
    """
    infos = archive.infolist()
    if len(infos) > max_entries:
        raise ValidationError(f"{format_name} has too many ZIP entries")
    total = sum(info.file_size for info in infos)
    compressed = max(1, sum(info.compress_size for info in infos))
    if total > max_uncompressed or total / compressed > max_ratio:
        raise ValidationError(f"{format_name} decompression limits exceeded")
