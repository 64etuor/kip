from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fetch_public_corpus.py"
MANIFEST = ROOT / "evaluation" / "corpus" / "public-government.json"
DATASET = ROOT / "evaluation" / "golden" / "public-government.yaml"


def _load_fetcher():
    spec = importlib.util.spec_from_file_location("fetch_public_corpus", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_corpus_manifest_is_licensed_and_reproducible() -> None:
    fetcher = _load_fetcher()
    manifest = fetcher.load_manifest(MANIFEST)

    assert manifest["schema_version"] == "kip.public-corpus.v1"
    assert len(manifest["documents"]) >= 6
    assert {entry["license"] for entry in manifest["documents"]} == {
        "KOGL-Type-1-Attribution"
    }
    for entry in manifest["documents"]:
        assert len(entry["sha256"]) == 64
        assert entry["filename"].endswith(".pdf")
        fetcher.validate_url(entry["url"])
        fetcher.validate_url(entry["source_page"])


def test_public_corpus_pdf_verification_rejects_tampering(tmp_path: Path) -> None:
    fetcher = _load_fetcher()
    target = tmp_path / "document.pdf"
    payload = b"%PDF-1.4\nKIP public corpus fixture\n"
    target.write_bytes(payload)

    fetcher.verify_pdf(target, hashlib.sha256(payload).hexdigest())
    with pytest.raises(fetcher.CorpusError, match="checksum"):
        fetcher.verify_pdf(target, "0" * 64)


def test_public_corpus_rejects_non_government_or_insecure_urls() -> None:
    fetcher = _load_fetcher()

    with pytest.raises(fetcher.CorpusError, match="HTTPS"):
        fetcher.validate_url("http://www.mohw.go.kr/file.pdf")
    with pytest.raises(fetcher.CorpusError, match="government"):
        fetcher.validate_url("https://example.com/file.pdf")


def test_example_configuration_keeps_public_corpus_opt_in() -> None:
    config = (ROOT / "config" / "kip.example.toml").read_text(encoding="utf-8")

    assert 'name = "public-government"' in config
    assert 'root = "./var/public-corpus"' in config
    public_source = config.split('name = "public-government"', maxsplit=1)[1]
    assert "enabled = false" in public_source.split("[sources.slack]", maxsplit=1)[0]


def test_public_corpus_golden_set_has_relevance_and_acl_coverage() -> None:
    dataset = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    relevance = [case for case in dataset["cases"] if case["category"] != "access_denied"]
    access_denied = [case for case in dataset["cases"] if case["category"] == "access_denied"]

    assert len(relevance) == 30
    assert len(access_denied) == 6
    assert all(case["expected_documents"] for case in relevance)
    assert all(not case["expected_documents"] and case["forbidden_documents"] for case in access_denied)
