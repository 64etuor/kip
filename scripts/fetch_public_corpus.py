#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_DOCUMENT_BYTES = 25 * 1024 * 1024


class CorpusError(RuntimeError):
    pass


def validate_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise CorpusError(f"corpus URLs must use HTTPS: {value}")
    hostname = (parsed.hostname or "").lower()
    if hostname != "go.kr" and not hostname.endswith(".go.kr"):
        raise CorpusError(f"corpus URLs must use a Korean government host: {value}")


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusError(f"cannot load corpus manifest {path}: {exc}") from exc
    if payload.get("schema_version") != "kip.public-corpus.v1":
        raise CorpusError("unsupported public corpus manifest schema")
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise CorpusError("public corpus manifest must contain documents")
    for entry in documents:
        if not isinstance(entry, dict):
            raise CorpusError("public corpus document entries must be objects")
        for field in (
            "id",
            "title",
            "agency",
            "filename",
            "url",
            "source_page",
            "license",
            "license_url",
            "sha256",
            "attribution",
        ):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise CorpusError(f"public corpus entry is missing {field}")
        filename = Path(entry["filename"])
        if filename.name != entry["filename"] or filename.suffix.lower() != ".pdf":
            raise CorpusError(f"unsafe public corpus filename: {entry['filename']}")
        if len(entry["sha256"]) != 64:
            raise CorpusError(f"invalid SHA-256 for {entry['filename']}")
        validate_url(entry["url"])
        validate_url(entry["source_page"])
        license_url = urlparse(entry["license_url"])
        if license_url.scheme != "https" or license_url.hostname != "www.kogl.or.kr":
            raise CorpusError(f"unsupported public corpus license URL: {entry['license_url']}")
    return payload


def verify_pdf(path: Path, expected_sha256: str) -> None:
    try:
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise CorpusError(f"download is not a PDF: {path}")
            handle.seek(0)
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise CorpusError(f"cannot read corpus file {path}: {exc}") from exc
    if digest != expected_sha256:
        raise CorpusError(
            f"checksum mismatch for {path.name}: expected {expected_sha256}, got {digest}"
        )


def _download(entry: dict[str, str], output_dir: Path, timeout: int) -> Path:
    target = output_dir / entry["filename"]
    if target.exists():
        verify_pdf(target, entry["sha256"])
        return target

    request = urllib.request.Request(
        entry["url"],
        headers={
            "User-Agent": "KIP-public-corpus/1.0 (+local evaluation)",
            "Referer": entry["source_page"],
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        },
    )
    temporary_path: Path | None = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DOCUMENT_BYTES:
                raise CorpusError(f"document exceeds size limit: {entry['filename']}")
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=output_dir,
                prefix=f".{entry['filename']}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                total = 0
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > MAX_DOCUMENT_BYTES:
                        raise CorpusError(f"document exceeds size limit: {entry['filename']}")
                    temporary.write(block)
        verify_pdf(temporary_path, entry["sha256"])
        os.replace(temporary_path, target)
        return target
    except (OSError, ValueError) as exc:
        if isinstance(exc, CorpusError):
            raise
        raise CorpusError(f"failed to fetch {entry['filename']}: {exc}") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Fetch and verify the licensed Korean public-sector evaluation corpus."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=root / "evaluation" / "corpus" / "public-government.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "var" / "public-corpus",
    )
    parser.add_argument("--check", action="store_true", help="verify existing files only")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    verified: list[dict[str, Any]] = []
    for raw_entry in manifest["documents"]:
        entry = {str(key): str(value) for key, value in raw_entry.items()}
        target = args.output_dir / entry["filename"]
        if args.check:
            verify_pdf(target, entry["sha256"])
        else:
            target = _download(entry, args.output_dir, args.timeout)
        verified.append(
            {
                "id": entry["id"],
                "file": str(target),
                "sha256": entry["sha256"],
                "license": entry["license"],
            }
        )
    print(
        json.dumps(
            {
                "schema_version": "kip.public-corpus-fetch.v1",
                "ok": True,
                "document_count": len(verified),
                "documents": verified,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
