from pathlib import Path

from kip.architecture_rules import (
    edge_dependency_violations,
    layer_dependency_violations,
)

ROOT = Path(__file__).resolve().parents[1]


def test_inner_layers_import_only_their_allow_list() -> None:
    # Given the domain, the ports and the application source trees
    # When their imports are checked against each layer's allow-list
    violations = layer_dependency_violations(ROOT)

    # Then no layer reaches a vendor SDK or an infrastructure module directly
    assert violations == []


def test_edges_import_neither_each_other_nor_adapters() -> None:
    # Given the CLI, REST, MCP, setup, packaging and worker entry points
    # When their imports are checked
    violations = edge_dependency_violations(ROOT)

    # Then every edge takes its adapters from kip.container and stays
    # independent of its siblings
    assert violations == []
