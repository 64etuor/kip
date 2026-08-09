#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kip.adapters.repository.memory import MemoryRepository  # noqa: E402
from kip.api import create_app  # noqa: E402
from kip.container import build_container  # noqa: E402
from kip.domain.egress import EgressDecision  # noqa: E402
from kip.domain.models import (  # noqa: E402
    ApprovedAssertion,
    Artifact,
    AssertionCandidate,
    AssertionExplanation,
    Capabilities,
    ConnectorEvent,
    ContentUnit,
    ContextBundle,
    ContextRequest,
    DocumentPacket,
    Envelope,
    EvidenceLocator,
    EvidenceRead,
    GraphNeighborsRequest,
    GraphPathRequest,
    IngestResult,
    SearchHit,
    SearchRequest,
    SourceObject,
    SourceRevision,
    StatusReport,
    SyncSummary,
    VocabularyItem,
    XlsxRangeRead,
)
from kip.settings import Settings  # noqa: E402
from kip.setup.models import (  # noqa: E402
    SetupAnswers,
    SetupInspection,
    SetupPlan,
    SetupReceipt,
)

MODELS = {
    "envelope": Envelope,
    "source-object": SourceObject,
    "source-revision": SourceRevision,
    "artifact": Artifact,
    "content-unit": ContentUnit,
    "evidence-locator": EvidenceLocator,
    "document-packet": DocumentPacket,
    "connector-event": ConnectorEvent,
    "search-request": SearchRequest,
    "search-hit": SearchHit,
    "context-request": ContextRequest,
    "context-bundle": ContextBundle,
    "graph-neighbors-request": GraphNeighborsRequest,
    "graph-path-request": GraphPathRequest,
    "assertion-candidate": AssertionCandidate,
    "approved-assertion": ApprovedAssertion,
    "assertion-explanation": AssertionExplanation,
    "xlsx-range-read": XlsxRangeRead,
    "evidence-read": EvidenceRead,
    "ingest-result": IngestResult,
    "sync-summary": SyncSummary,
    "capabilities": Capabilities,
    "status-report": StatusReport,
    "vocabulary-item": VocabularyItem,
    "egress-decision": EgressDecision,
    "setup-answers": SetupAnswers,
    "setup-inspection": SetupInspection,
    "setup-plan": SetupPlan,
    "setup-receipt": SetupReceipt,
}


def render_files() -> dict[Path, str]:
    files: dict[Path, str] = {}
    for name, model in MODELS.items():
        path = ROOT / "contracts" / f"{name}.schema.json"
        text = json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        files[path] = text

    settings = Settings.for_test()
    container = build_container(settings, repository=MemoryRepository())
    app = create_app(container)
    openapi = app.openapi()
    files[ROOT / "contracts/openapi.json"] = json.dumps(openapi, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    files[ROOT / "contracts/openapi.yaml"] = yaml.safe_dump(openapi, allow_unicode=True, sort_keys=False)
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render_files()
    mismatches: list[str] = []
    for path, text in expected.items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                mismatches.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(path.relative_to(ROOT))
    if mismatches:
        print("Generated contracts are stale:", file=sys.stderr)
        for item in mismatches:
            print(f"  - {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
