from __future__ import annotations

from kip.errors import DependencyUnavailableError


class Neo4jProjectionAdapter:
    """Optional read projection. Never use as the canonical assertion store."""

    name = "neo4j"

    def __init__(self, uri: str, username: str, password: str) -> None:
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise DependencyUnavailableError("Install the neo4j extra") from exc
        self.driver = GraphDatabase.driver(uri, auth=(username, password))

    def rebuild(self, context):
        raise NotImplementedError("Implement only after the Neo4j adoption and parity gate")

    def neighbors(self, context, request):
        raise NotImplementedError("Use the PostgreSQL graph adapter until the adoption gate")

    def paths(self, context, request):
        raise NotImplementedError("Use the PostgreSQL graph adapter until the adoption gate")
