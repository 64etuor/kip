"""Unit tests for the PostgreSQL interaction store's spec (de)serialization.

`PostgresInteractionStore._spec_payload` and `_candidate` are pure functions
(no live connection needed) that decide how the seven `OntologyDiscovery*`
release-spec fields (`parent`, `domain`, `range`, `inverse`, `risk`, `review`,
`extraction`) round-trip through the nullable `proposal_spec` jsonb column.
These tests exercise that logic directly, without requiring
`KIP_TEST_POSTGRES_URL` / a live PostgreSQL instance.
"""

from __future__ import annotations

from datetime import UTC, datetime

from kip.adapters.repository.postgres.interactions import _candidate, _spec_payload
from kip.domain.interactions import OntologyDiscoveryCandidate

_NOW = datetime(2026, 8, 10, tzinfo=UTC)


def _candidate_model(**overrides: object) -> OntologyDiscoveryCandidate:
    fields: dict[str, object] = {
        "domain_profile": "empty",
        "kind": "predicate",
        "symbol": "funds",
        "label": "지원한다",
        "definition": "한 조직이 다른 프로젝트를 지원한다.",
        "fingerprint": "sha256:test",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(overrides)
    return OntologyDiscoveryCandidate(**fields)  # type: ignore[arg-type]


def test_spec_payload_is_none_when_every_spec_field_is_absent() -> None:
    candidate = _candidate_model(kind="entity_type", symbol="contract")

    assert _spec_payload(candidate) is None


def test_spec_payload_captures_every_predicate_spec_field() -> None:
    candidate = _candidate_model(
        domain=["Organization"],
        range=["Project"],
        inverse="funded_by",
        risk="medium",
        review="conditional",
        extraction="mixed",
    )

    assert _spec_payload(candidate) == {
        "parent": None,
        "domain": ["Organization"],
        "range": ["Project"],
        "inverse": "funded_by",
        "risk": "medium",
        "review": "conditional",
        "extraction": "mixed",
    }


def test_spec_payload_captures_an_entity_type_parent_alone() -> None:
    candidate = _candidate_model(
        kind="entity_type",
        symbol="contract",
        parent="no_such_entity_type",
    )

    assert _spec_payload(candidate) == {
        "parent": "no_such_entity_type",
        "domain": None,
        "range": None,
        "inverse": None,
        "risk": None,
        "review": None,
        "extraction": None,
    }


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "odc_test",
        "domain_profile": "empty",
        "kind": "predicate",
        "symbol": "funds",
        "label": "지원한다",
        "definition": "한 조직이 다른 프로젝트를 지원한다.",
        "target_symbol": None,
        "proposal_spec": None,
        "fingerprint": "sha256:test",
        "status": "proposed",
        "occurrence_count": 1,
        "created_at": _NOW,
        "updated_at": _NOW,
        "reviewed_at": None,
        "reviewed_by": None,
        "review_note": None,
    }
    base.update(overrides)
    return base


def test_candidate_defaults_every_spec_field_to_none_when_proposal_spec_is_null() -> None:
    # A legacy row (written before the `proposal_spec` migration, or a
    # proposal with no spec fields) must behave exactly as it did before:
    # release-time fallback defaults apply, not a materialized value.
    candidate = _candidate(_row(proposal_spec=None))

    assert candidate.parent is None
    assert candidate.domain is None
    assert candidate.range is None
    assert candidate.inverse is None
    assert candidate.risk is None
    assert candidate.review is None
    assert candidate.extraction is None


def test_candidate_reads_back_every_persisted_predicate_spec_field() -> None:
    candidate = _candidate(
        _row(
            proposal_spec={
                "parent": None,
                "domain": ["Organization"],
                "range": ["Project"],
                "inverse": "funded_by",
                "risk": "medium",
                "review": "conditional",
                "extraction": "mixed",
            }
        )
    )

    assert candidate.domain == ["Organization"]
    assert candidate.range == ["Project"]
    assert candidate.inverse == "funded_by"
    assert candidate.risk == "medium"
    assert candidate.review == "conditional"
    assert candidate.extraction == "mixed"


def test_candidate_reads_back_an_explicitly_invalid_parent_without_degrading_it() -> None:
    # An invalid (unknown) `parent` must survive the round trip unchanged so
    # review-time shadow validation can reject it later, instead of the
    # store silently dropping it (which would let materialization fall back
    # to the lenient `target_symbol` hint and silently create a root type).
    candidate = _candidate(
        _row(
            kind="entity_type",
            target_symbol="no_such_entity_type",
            proposal_spec={
                "parent": "no_such_entity_type",
                "domain": None,
                "range": None,
                "inverse": None,
                "risk": None,
                "review": None,
                "extraction": None,
            },
        )
    )

    assert candidate.parent == "no_such_entity_type"


def test_candidate_reads_back_a_proposal_spec_serialized_as_a_json_string() -> None:
    # psycopg may hand back a jsonb column as an already-decoded dict or as
    # a raw JSON string depending on adapter registration; `_candidate` must
    # handle both, matching the existing `_json_value` convention used for
    # `choices`/`values` elsewhere in this module.
    candidate = _candidate(
        _row(
            proposal_spec=(
                '{"parent": null, "domain": ["Organization"], "range": ["Project"], '
                '"inverse": null, "risk": "medium", "review": "conditional", '
                '"extraction": "mixed"}'
            )
        )
    )

    assert candidate.domain == ["Organization"]
    assert candidate.risk == "medium"
