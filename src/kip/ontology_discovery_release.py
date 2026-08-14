"""Materialize an approved ontology discovery candidate into the ontology tree.

This module never runs as part of normal retrieval or ingestion. It is only
invoked from the approval path of `review_ontology_discovery_candidate`
(`kip.application.interactions.InteractionUseCases`) and only for `kind in
{"entity_type", "predicate"}`. `controlled_value` and `alias` candidates are
left as manual releases; this module does not touch them.

Release strategy, per the "shadow, validate, then atomically replace" rule
used elsewhere for extraction/index rebuilds (see AGENTS.md rule 12):

1. Copy the whole `ontology/` tree to a temporary directory.
2. Apply the proposed text edits to the copy only.
3. Run `kip.ontology.validate_ontology` against the copy. Any error aborts
   the release before a single real file is touched.
4. Only then, atomically replace each changed real file with `os.replace`.

Comment preservation is mandatory: `core/predicates.yaml` and the domain
profile files carry hand-written comment headers. This module never
round-trips file contents through `yaml.dump` (which would silently drop
them); it only appends a correctly indented text block under an existing (or
newly created) top-level key and rewrites the `version:` line in place.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml

from kip.domain.interactions import (
    DiscoveryKind,
    OntologyDiscoveryCandidate,
    OntologyDiscoveryRelease,
)
from kip.errors import ValidationError
from kip.ontology import domain_profile_path, validate_ontology

# Predicates in `ontology/core/predicates.yaml` describe evidence-linking
# relations whose domain/range are almost always `EvidenceObject` and its
# descendants (Document, Communication, Conversation via `parent` chains) or
# a small closed set of business objects. When a discovery proposal omits an
# explicit domain/range, `EvidenceObject` is the broadest entity type that
# still describes an evidence-bearing relation without silently widening the
# predicate to non-evidence types (Person, Organization, Project, ...), so it
# is the safest default root. `OntologyCatalog.is_a` walks the `parent` chain,
# so any subject/object whose type descends from `EvidenceObject` (including
# every core `Document`/`Communication`/`Conversation` subtype and every
# domain-profile subtype declared with `parent: EvidenceObject` or a
# descendant) is automatically covered without listing each subtype here.
_DEFAULT_PREDICATE_ROOT = "EvidenceObject"
_DEFAULT_PREDICATE_RISK = "high"
_DEFAULT_PREDICATE_REVIEW = "required"
_DEFAULT_PREDICATE_EXTRACTION = "semantic"

_VERSION_LINE_RE = re.compile(r"^version:\s*([0-9]+)\.([0-9]+)\.([0-9]+)\s*$")


def materialize_ontology_release(
    ontology_root: Path,
    domain_profile: str,
    candidate: OntologyDiscoveryCandidate,
) -> OntologyDiscoveryRelease:
    """Materialize `candidate` into the ontology tree, or return the existing release.

    Raises `ValidationError` if the ontology root is read-only, the release
    would fail contract validation, or `candidate.kind` is not releasable
    automatically. Never leaves a half-written tree: on failure, no real file
    under `ontology_root` is modified.
    """
    if candidate.kind == "entity_type":
        return _materialize_entity_type(ontology_root, domain_profile, candidate)
    if candidate.kind == "predicate":
        return _materialize_predicate(ontology_root, candidate)
    raise ValidationError(
        f"ontology discovery kind {candidate.kind!r} does not release automatically"
    )


def _materialize_entity_type(
    ontology_root: Path,
    domain_profile: str,
    candidate: OntologyDiscoveryCandidate,
) -> OntologyDiscoveryRelease:
    domain_path = domain_profile_path(ontology_root, domain_profile)
    _require_writable(ontology_root, domain_path)
    text = domain_path.read_text(encoding="utf-8")
    payload = yaml.safe_load(text) or {}
    existing_entity_types = payload.get("entity_types", {})
    if not isinstance(existing_entity_types, dict):
        existing_entity_types = {}
    current_version = str(payload.get("version", "1.0.0"))
    relative_path = str(domain_path.relative_to(ontology_root))
    if candidate.symbol in existing_entity_types:
        return _release("entity_type", candidate.symbol, relative_path, current_version)

    core_path = ontology_root / "core" / "entity-types.yaml"
    core_payload = yaml.safe_load(core_path.read_text(encoding="utf-8")) or {}
    core_entity_types = core_payload.get("entity_types", {})
    known_entity_types = set(
        core_entity_types if isinstance(core_entity_types, dict) else {}
    ) | set(existing_entity_types)

    parent = candidate.parent
    if parent is None:
        # Legacy/implicit hint: only honored if it already names a known
        # type, otherwise the new type silently becomes a root.
        hint = candidate.target_symbol
        parent = hint if hint is not None and hint in known_entity_types else None

    block = _entity_type_block(candidate.symbol, parent, candidate.label, candidate.definition)
    new_version = _bump_minor(current_version)
    new_text = _set_version_line(text, new_version)
    new_text = _insert_top_level_block(new_text, "entity_types", block)

    _apply_via_shadow(ontology_root, domain_profile, {domain_path: new_text})
    return _release("entity_type", candidate.symbol, relative_path, new_version)


def _materialize_predicate(
    ontology_root: Path,
    candidate: OntologyDiscoveryCandidate,
) -> OntologyDiscoveryRelease:
    predicates_path = ontology_root / "core" / "predicates.yaml"
    _require_writable(ontology_root, predicates_path)
    text = predicates_path.read_text(encoding="utf-8")
    payload = yaml.safe_load(text) or {}
    existing_predicates = payload.get("predicates", {})
    if not isinstance(existing_predicates, dict):
        existing_predicates = {}
    current_version = str(payload.get("version", "1.0.0"))
    relative_path = str(predicates_path.relative_to(ontology_root))
    if candidate.symbol in existing_predicates:
        return _release("predicate", candidate.symbol, relative_path, current_version)

    domain = candidate.domain or [_DEFAULT_PREDICATE_ROOT]
    range_ = candidate.range or [_DEFAULT_PREDICATE_ROOT]
    inverse = candidate.inverse
    risk = candidate.risk or _DEFAULT_PREDICATE_RISK
    review = candidate.review or _DEFAULT_PREDICATE_REVIEW
    extraction = candidate.extraction or _DEFAULT_PREDICATE_EXTRACTION

    block = _predicate_block(
        candidate.symbol,
        label=candidate.label,
        definition=candidate.definition,
        domain=domain,
        range_=range_,
        inverse=inverse,
        risk=risk,
        review=review,
        extraction=extraction,
    )
    new_version = _bump_minor(current_version)
    new_predicates_text = _set_version_line(text, new_version)
    new_predicates_text = _insert_top_level_block(new_predicates_text, "predicates", block)

    edits: dict[Path, str] = {predicates_path: new_predicates_text}
    if review == "required":
        review_policy_path = ontology_root / "policies" / "review-policy.yaml"
        _require_writable(ontology_root, review_policy_path)
        review_policy_text = review_policy_path.read_text(encoding="utf-8")
        edits[review_policy_path] = _insert_nested_list_item(
            review_policy_text,
            parent_key="human_review_required",
            list_key="predicates",
            item=candidate.symbol,
        )

    domain_profile_for_shadow = _any_domain_profile(ontology_root)
    _apply_via_shadow(ontology_root, domain_profile_for_shadow, edits)
    return _release("predicate", candidate.symbol, relative_path, new_version)


def _release(kind: DiscoveryKind, symbol: str, file: str, version: str) -> OntologyDiscoveryRelease:
    return OntologyDiscoveryRelease(
        kind=kind,
        symbol=symbol,
        file=file,
        version=version,
        # No consuming service holds a hot-swappable ontology reference yet
        # (`KnowledgeUseCases`, `OntologyRagUseCases`, and the relation miner
        # each capture an immutable `OntologyCatalog` snapshot at container
        # build time). The written files are correct and every fresh process
        # (every CLI invocation, and any restarted API/MCP server) picks them
        # up immediately; a long-running API/MCP process needs a restart to
        # see the new symbol outside of the discovery module itself.
        catalog_refresh="restart_required",
    )


def _any_domain_profile(ontology_root: Path) -> str:
    """Pick a domain profile file present on disk to run shadow validation.

    Predicate releases do not touch a domain profile file, but
    `validate_ontology` requires a domain profile to validate the tree as a
    whole; any profile shipped in `ontology/domains/` is representative.
    """
    domains_dir = ontology_root / "domains"
    candidates = sorted(domains_dir.glob("*.yaml")) if domains_dir.is_dir() else []
    if not candidates:
        raise ValidationError(f"no ontology domain profile found under {domains_dir}")
    return candidates[0].stem


def _require_writable(ontology_root: Path, target_file: Path) -> None:
    if not os.access(ontology_root, os.W_OK) or not os.access(target_file.parent, os.W_OK):
        raise ValidationError(
            "ontology root is not writable; run the release from a writable "
            "checkout or mount ontology/ writable"
        )


def _apply_via_shadow(
    ontology_root: Path,
    domain_profile: str,
    edits: dict[Path, str],
) -> None:
    for real_path, new_text in edits.items():
        try:
            yaml.safe_load(new_text)
        except yaml.YAMLError as exc:
            raise ValidationError(
                f"ontology release produced invalid YAML for {real_path.name}: {exc}"
            ) from exc
    with tempfile.TemporaryDirectory(prefix="kip-ontology-release-") as tmp:
        shadow_root = Path(tmp) / "ontology"
        shutil.copytree(ontology_root, shadow_root)
        for real_path, new_text in edits.items():
            shadow_path = shadow_root / real_path.relative_to(ontology_root)
            shadow_path.write_text(new_text, encoding="utf-8")
        errors = validate_ontology(shadow_root, domain_profile=domain_profile)
        if errors:
            raise ValidationError(
                "ontology release failed shadow validation: " + "; ".join(errors)
            )
    for real_path, new_text in edits.items():
        _atomic_write(real_path, new_text)


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def _bump_minor(version: str) -> str:
    match = _VERSION_LINE_RE.fullmatch(f"version: {version}")
    if match is None:
        raise ValidationError(f"ontology file version is not semantic x.y.z: {version!r}")
    major, minor, _patch = match.groups()
    return f"{major}.{int(minor) + 1}.0"


def _set_version_line(text: str, new_version: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _VERSION_LINE_RE.fullmatch(line.rstrip("\n")):
            lines[index] = f"version: {new_version}"
            return _join(lines, text)
    raise ValidationError("ontology file is missing a version: line")


def _insert_top_level_block(text: str, key: str, block_lines: list[str]) -> str:
    lines = text.splitlines()
    key_line = f"{key}:"
    key_index = next((i for i, line in enumerate(lines) if line.rstrip() == key_line), None)
    if key_index is not None:
        insert_at = _block_end(lines, key_index, key_indent=0)
        lines[insert_at:insert_at] = block_lines
        return _join(lines, text)
    # An empty section is often written in flow style (`entity_types: {}`)
    # by whatever last generated the file. Turn it into block style in
    # place rather than appending a second, duplicate top-level key, which
    # `yaml.safe_load` would silently resolve by keeping only the last one.
    empty_flow_line = f"{key}: {{}}"
    empty_index = next(
        (i for i, line in enumerate(lines) if line.rstrip() == empty_flow_line), None
    )
    if empty_index is not None:
        lines[empty_index] = key_line
        lines[empty_index + 1 : empty_index + 1] = block_lines
        return _join(lines, text)
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(key_line)
    lines.extend(block_lines)
    return _join(lines, text)


def _insert_nested_list_item(text: str, *, parent_key: str, list_key: str, item: str) -> str:
    lines = text.splitlines()
    parent_index = next(
        (i for i, line in enumerate(lines) if line.rstrip() == f"{parent_key}:"), None
    )
    if parent_index is None:
        raise ValidationError(f"ontology policy file is missing top-level key {parent_key!r}")
    parent_end = _block_end(lines, parent_index, key_indent=0)
    list_index = next(
        (
            i
            for i in range(parent_index + 1, parent_end)
            if lines[i].strip() == f"{list_key}:"
        ),
        None,
    )
    item_line = f"    - {item}"
    if list_index is None:
        insert_at = parent_end
        lines[insert_at:insert_at] = [f"  {list_key}:", item_line]
    else:
        insert_at = _block_end(lines, list_index, key_indent=2)
        lines[insert_at:insert_at] = [item_line]
    return _join(lines, text)


def _block_end(lines: list[str], key_index: int, *, key_indent: int) -> int:
    for index in range(key_index + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= key_indent:
            return index
    return len(lines)


def _join(lines: list[str], original_text: str) -> str:
    joined = "\n".join(lines)
    return joined + "\n" if original_text.endswith("\n") else joined


def _yaml_scalar(value: str) -> str:
    # JSON string literals are valid YAML flow scalars and correctly escape
    # colons, quotes, backslashes, and Unicode without the trailing
    # `...`/document-end markers `yaml.safe_dump` adds for some inputs.
    return json.dumps(value, ensure_ascii=False)


def _entity_type_block(symbol: str, parent: str | None, label: str, definition: str) -> list[str]:
    lines = [f"  {symbol}:"]
    if parent is not None:
        lines.append(f"    parent: {parent}")
    lines.append(f"    label_ko: {_yaml_scalar(label)}")
    lines.append(f"    description_ko: {_yaml_scalar(definition)}")
    return lines


def _predicate_block(
    symbol: str,
    *,
    label: str,
    definition: str,
    domain: list[str],
    range_: list[str],
    inverse: str | None,
    risk: str,
    review: str,
    extraction: str,
) -> list[str]:
    domain_literal = "[" + ", ".join(domain) + "]"
    range_literal = "[" + ", ".join(range_) + "]"
    inverse_literal = inverse if inverse is not None else "null"
    return [
        f"  {symbol}:",
        f"    description: {_yaml_scalar(definition)}",
        f"    label_ko: {_yaml_scalar(label)}",
        f"    domain: {domain_literal}",
        f"    range: {range_literal}",
        f"    inverse: {inverse_literal}",
        f"    risk: {risk}",
        f"    extraction: {extraction}",
        f"    review: {review}",
    ]
