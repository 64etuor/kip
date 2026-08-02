from __future__ import annotations

from pathlib import Path

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.settings import Settings


@pytest.fixture()
def test_container(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "app": {"workspace": "default"},
            "search": {"semantic_enabled": False, "korean_ngram_min": 2, "korean_ngram_max": 4},
            "graph": {"backend": "memory"},
            "sources": {
                "filesystem": [
                    {
                        "name": "fixture",
                        "root": str(source_root),
                        "enabled": True,
                        "read_only": True,
                        "settle_seconds": 0,
                        "include_extensions": [".txt", ".md", ".xlsx", ".pdf", ".hwp", ".hwpx", ".docx"],
                        "exclude_globs": [],
                        "acl_scope": "workspace:default",
                    }
                ]
            },
            "parsers": {"hwp": {"order": ["paired_pdf"]}},
        },
        environment="test",
        workspace="default",
        database_url="memory://",
        cas_path=tmp_path / "cas",
        api_key="test-key",
        admin_key="test-admin",
    )
    return build_container(settings, repository=MemoryRepository())
