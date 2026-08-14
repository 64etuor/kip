# noqa: SIZE_OK — the copyable SDK is intentionally distributed as one file
"""Small dependency-light client for applications integrating with KIP REST.

Copy this file into an application or install the project package. The public
HTTP contract, not this implementation, is the compatibility boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

SearchMode = Literal["lexical", "vector", "hybrid", "reranked"]


class KipApiError(RuntimeError):
    pass


def _require_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise KipApiError("KIP response data is not a JSON object")
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _require_object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise KipApiError("KIP response data is not a JSON object list")
    return [_require_object(item) for item in value]


def _retrieval_payload(
    query: str,
    limit: int,
    *,
    max_chars: int | None = None,
    mode: SearchMode | None = None,
    source_kinds: list[str] | None = None,
    document_types: list[str] | None = None,
    project_ids: list[str] | None = None,
    include_candidate_assertions: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query, "limit": limit}
    optional = {
        "mode": mode,
        "source_kinds": source_kinds,
        "document_types": document_types,
        "project_ids": project_ids,
    }
    if max_chars is not None:
        payload["max_chars"] = max_chars
    payload.update({key: value for key, value in optional.items() if value is not None})
    if include_candidate_assertions:
        payload["include_candidate_assertions"] = True
    return payload


@dataclass(slots=True)  # noqa: MUTABLE_OK — callers may rotate credentials between requests
class KipClient:
    base_url: str = "http://127.0.0.1:8080"
    api_key: str = ""
    bearer_token: str = ""
    admin_key: str = ""
    workspace: str = "default"
    principal_id: str = "application"
    acl_scopes: list[str] = field(default_factory=lambda: ["workspace:default"])
    send_legacy_identity_headers: bool = False
    timeout: float = 30.0

    def _headers(self, *, admin: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.send_legacy_identity_headers:
            headers.update(
                {
                    "X-KIP-Workspace": self.workspace,
                    "X-KIP-Principal": self.principal_id,
                    "X-KIP-ACL-Scopes": ",".join(self.acl_scopes),
                }
            )
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.api_key:
            headers["X-KIP-API-Key"] = self.api_key
        if admin and self.admin_key:
            headers["X-KIP-Admin-Key"] = self.admin_key
        return headers

    def _request(self, method: str, path: str, *, admin: bool = False, **kwargs: Any) -> Any:
        with httpx.Client(
            base_url=self.base_url, timeout=self.timeout, headers=self._headers(admin=admin)
        ) as client:
            response = client.request(method, path, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise KipApiError(f"KIP returned non-JSON HTTP {response.status_code}") from exc
        if response.is_error:
            raise KipApiError(f"KIP HTTP {response.status_code}: {payload}")
        if isinstance(payload, dict) and payload.get("ok") is False:
            error = payload.get("error") or {}
            raise KipApiError(str(error.get("message") or error))
        return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload

    def capabilities(self) -> dict[str, Any]:
        return _require_object(self._request("GET", "/v1/capabilities"))

    def status(self) -> dict[str, Any]:
        return _require_object(self._request("GET", "/v1/status"))

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        mode: SearchMode | None = None,
        source_kinds: list[str] | None = None,
        document_types: list[str] | None = None,
        project_ids: list[str] | None = None,
        include_candidate_assertions: bool = False,
    ) -> list[dict[str, Any]]:
        return _require_object_list(
            self._request(
                "POST",
                "/v1/search",
                json=_retrieval_payload(
                    query,
                    limit,
                    mode=mode,
                    source_kinds=source_kinds,
                    document_types=document_types,
                    project_ids=project_ids,
                    include_candidate_assertions=include_candidate_assertions,
                ),
            )
        )

    def context(
        self,
        query: str,
        *,
        limit: int = 5,
        max_chars: int = 40000,
        mode: SearchMode | None = None,
        source_kinds: list[str] | None = None,
        document_types: list[str] | None = None,
        project_ids: list[str] | None = None,
        include_candidate_assertions: bool = False,
    ) -> dict[str, Any]:
        return _require_object(
            self._request(
                "POST",
                "/v1/context",
                json=_retrieval_payload(
                    query,
                    limit,
                    max_chars=max_chars,
                    mode=mode,
                    source_kinds=source_kinds,
                    document_types=document_types,
                    project_ids=project_ids,
                    include_candidate_assertions=include_candidate_assertions,
                ),
            )
        )

    def answer(
        self,
        query: str,
        *,
        limit: int = 5,
        max_chars: int = 12000,
        mode: SearchMode | None = None,
        source_kinds: list[str] | None = None,
        document_types: list[str] | None = None,
        project_ids: list[str] | None = None,
        include_candidate_assertions: bool = False,
    ) -> dict[str, Any]:
        return _require_object(
            self._request(
                "POST",
                "/v1/answer",
                json=_retrieval_payload(
                    query,
                    limit,
                    max_chars=max_chars,
                    mode=mode,
                    source_kinds=source_kinds,
                    document_types=document_types,
                    project_ids=project_ids,
                    include_candidate_assertions=include_candidate_assertions,
                ),
            )
        )

    def read_unit(self, unit_id: str) -> dict[str, Any]:
        return _require_object(self._request("GET", f"/v1/units/{unit_id}"))

    def read_xlsx_range(
        self, artifact_id: str, sheet: str, cell_range: str, *, allow_stale: bool = False
    ) -> dict[str, Any]:
        return _require_object(
            self._request(
                "GET",
                f"/v1/xlsx/{artifact_id}/range",
                params={"sheet": sheet, "cell_range": cell_range, "allow_stale": allow_stale},
            )
        )

    def get_assertion(self, assertion_id: str) -> dict[str, Any]:
        return _require_object(self._request("GET", f"/v1/assertions/{assertion_id}"))

    def explain_assertion(self, assertion_id: str) -> dict[str, Any]:
        return _require_object(self._request("GET", f"/v1/assertions/{assertion_id}/explain"))

    def enqueue_filesystem_sync(self, source_name: str) -> dict[str, Any]:
        return _require_object(
            self._request("POST", f"/v1/sync/filesystem/{source_name}", admin=True)
        )

    def enqueue_sync(self, source_name: str) -> dict[str, Any]:
        return _require_object(self._request("POST", f"/v1/sync/{source_name}", admin=True))

    def list_jobs(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        return _require_object_list(self._request("GET", "/v1/jobs", admin=True, params=params))

    def post_connector_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Ingest one canonical connector event using the admin credential."""
        return _require_object(
            self._request("POST", "/v1/connectors/events", admin=True, json=event)
        )

    def list_review_candidates(
        self,
        *,
        status: str = "proposed",
        limit: int = 100,
        predicate: str | None = None,
        subject_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the `kip.assertion-candidate-listing.v1` review listing."""
        params: dict[str, Any] = {"status": status, "limit": limit}
        if predicate is not None:
            params["predicate"] = predicate
        if subject_id is not None:
            params["subject_id"] = subject_id
        return _require_object(
            self._request(
                "GET",
                "/v1/review/candidates",
                admin=True,
                params=params,
            )
        )

    def approve_review_candidate(
        self,
        candidate_id: str,
        *,
        note: str | None = None,
        supersede_contradicted: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if note is not None:
            params["note"] = note
        if supersede_contradicted:
            params["supersede_contradicted"] = "true"
        return _require_object(
            self._request(
                "POST",
                f"/v1/review/candidates/{candidate_id}/approve",
                admin=True,
                params=params or None,
            )
        )

    def revoke_assertion(self, assertion_id: str, *, note: str) -> dict[str, Any]:
        """Revoke an approved assertion; a non-empty note is required."""
        return _require_object(
            self._request(
                "POST",
                f"/v1/review/assertions/{assertion_id}/revoke",
                admin=True,
                params={"note": note},
            )
        )

    def reject_review_candidate(
        self,
        candidate_id: str,
        *,
        note: str | None = None,
    ) -> dict[str, Any]:
        return _require_object(
            self._request(
                "POST",
                f"/v1/review/candidates/{candidate_id}/reject",
                admin=True,
                params={"note": note} if note is not None else None,
            )
        )

    def list_ontology_entities(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return _require_object_list(
            self._request(
                "GET",
                "/v1/ontology/entities",
                admin=True,
                params={"limit": limit},
            )
        )

    def ontology_context(self, query: str) -> dict[str, Any] | None:
        value = self._request(
            "POST",
            "/v1/ontology/context",
            json={"query": query},
        )
        return None if value is None else _require_object(value)

    def create_ontology_entity(self, entity: dict[str, Any]) -> dict[str, Any]:
        return _require_object(
            self._request(
                "POST",
                "/v1/ontology/entities",
                admin=True,
                json=entity,
            )
        )

    def enqueue_ontology_mining(self, unit_ids: list[str]) -> dict[str, Any]:
        return _require_object(
            self._request(
                "POST",
                "/v1/ontology/mining-jobs",
                admin=True,
                json={"unit_ids": unit_ids},
            )
        )

    def list_entity_candidates(
        self,
        *,
        status: str = "proposed",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return _require_object_list(
            self._request(
                "GET",
                "/v1/ontology/entity-candidates",
                admin=True,
                params={"status": status, "limit": limit},
            )
        )

    def approve_entity_candidate(
        self,
        candidate_id: str,
        *,
        note: str | None = None,
    ) -> dict[str, Any]:
        return _require_object(
            self._request(
                "POST",
                f"/v1/ontology/entity-candidates/{candidate_id}/approve",
                admin=True,
                params={"note": note} if note is not None else None,
            )
        )

    def reject_entity_candidate(
        self,
        candidate_id: str,
        *,
        note: str | None = None,
    ) -> dict[str, Any]:
        return _require_object(
            self._request(
                "POST",
                f"/v1/ontology/entity-candidates/{candidate_id}/reject",
                admin=True,
                params={"note": note} if note is not None else None,
            )
        )
