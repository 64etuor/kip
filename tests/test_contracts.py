import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_generated_contracts_exist():
    openapi = json.loads((ROOT / "contracts/openapi.json").read_text(encoding="utf-8"))
    assert openapi["openapi"].startswith("3.")
    assert "/v1/search" in openapi["paths"]
    assert (ROOT / "contracts/document-packet.schema.json").is_file()
    assert (ROOT / "contracts/connector-event.schema.json").is_file()
