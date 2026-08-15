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

Concurrency and crash safety, single-host deployment:

1. `materialize_ontology_release` serializes the whole read-modify-write
   cycle per ontology root with an OS-level advisory lock
   (`fcntl.flock` on `<ontology_root>/.release.lock`), so two
   near-simultaneous approvals can never race and silently drop one release
   (lost update). The lock blocks with a bounded timeout and fails closed
   with a `ValidationError` rather than hanging forever.
2. A predicate release with `review: required` touches two files
   (`core/predicates.yaml` and `policies/review-policy.yaml`) that must
   change together for `validate_ontology`'s exact-match invariant to hold.
   `_apply_via_shadow` journals the full new contents of every file it is
   about to replace to `<ontology_root>/.pending-release.json` (fsynced)
   before doing the `os.replace` sequence, and deletes the journal once every
   file has landed. If the process crashes between the two replaces, the
   journal survives and `complete_pending_release` (called at the start of
   every materialization, and once at container start-up before the eager
   `OntologyCatalog.load`) re-applies it idempotently, healing the tree
   without operator intervention.
3. `complete_pending_release` never trusts a journal blindly. A corrupt-JSON
   or structurally malformed journal (including a path-traversal attempt in
   a journaled file key) is renamed aside to
   `<ontology_root>/.pending-release.json.rejected` and a clear
   `ValidationError` is raised instead of crashing every future container
   start-up. A journal that is valid JSON but would, once applied, fail
   `validate_ontology` (e.g. a hand-edited or torn journal) is applied to a
   shadow copy of the tree first; on failure it is quarantined the same way
   and the real tree is left untouched. Only a journal that both parses and
   passes shadow validation is applied to the real files.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import yaml

from kip.domain.interactions import (
    DiscoveryKind,
    OntologyDiscoveryCandidate,
    OntologyDiscoveryRelease,
)
from kip.errors import ConflictError, ValidationError
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

_VERSION_LINE_RE = re.compile(
    r"^version:\s*([0-9]+)\.([0-9]+)\.([0-9]+)\s*(?P<comment>#.*)?$"
)

# Sentinel files under `ontology_root` used for release serialization and
# crash recovery. Neither is a `*.yaml` file, so `validate_ontology`'s
# `root.rglob("*.yaml")` scan (and any shadow copy of the tree) never sees
# them.
RELEASE_LOCK_FILENAME = ".release.lock"
RELEASE_JOURNAL_FILENAME = ".pending-release.json"
# Suffix a corrupt or invalid journal is renamed to so it stops being picked
# up by `has_pending_release`/`complete_pending_release` on every subsequent
# start-up. Not a `*.yaml` file either, so it is invisible to
# `validate_ontology`'s tree scan and to any shadow copy of the tree.
REJECTED_RELEASE_JOURNAL_SUFFIX = ".rejected"
_LOCK_TIMEOUT_SECONDS = 10.0
_LOCK_POLL_INTERVAL_SECONDS = 0.05

_MATERIALIZED_KINDS = frozenset({"entity_type", "predicate"})


def materialize_ontology_release(
    ontology_root: Path,
    domain_profile: str,
    candidate: OntologyDiscoveryCandidate,
) -> OntologyDiscoveryRelease:
    """Materialize `candidate` into the ontology tree, or return the existing release.

    Raises `ValidationError` if the ontology root is read-only, the release
    would fail contract validation, the release lock cannot be acquired
    within the timeout, or `candidate.kind` is not releasable automatically.
    Raises `ConflictError` if `candidate.symbol` was already released with
    different content by a different candidate. Never leaves a half-written
    tree: on failure, no real file under `ontology_root` is modified.
    """
    if candidate.kind not in _MATERIALIZED_KINDS:
        raise ValidationError(
            f"ontology discovery kind {candidate.kind!r} does not release automatically"
        )
    with _release_lock(ontology_root):
        # Heal a journal left behind by a crashed release before attempting
        # a new one, so a half-applied two-file predicate release from an
        # earlier process never masks or gets clobbered by this one.
        complete_pending_release(ontology_root)
        if candidate.kind == "entity_type":
            return _materialize_entity_type(ontology_root, domain_profile, candidate)
        return _materialize_predicate(ontology_root, domain_profile, candidate)


@contextlib.contextmanager
def _release_lock(
    ontology_root: Path, *, timeout: float = _LOCK_TIMEOUT_SECONDS
) -> Iterator[None]:
    """Serialize ontology release materialization for one ontology root.

    Single-host deployment: an `fcntl.flock` advisory lock on a sentinel
    file is sufficient (see module docstring) to prevent the
    read-modify-write race between two near-simultaneous approvals. Blocks
    up to `timeout` seconds, then fails closed with a `ValidationError`
    instead of hanging forever.
    """
    lock_path = ontology_root / RELEASE_LOCK_FILENAME
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        raise ValidationError(
            "ontology root is not writable; run the release from a writable "
            "checkout or mount ontology/ writable"
        ) from exc
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValidationError(
                        "timed out waiting for the ontology release lock; "
                        "another release is in progress"
                    ) from None
                time.sleep(_LOCK_POLL_INTERVAL_SECONDS)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def has_pending_release(ontology_root: Path) -> bool:
    """Whether a crashed release left a journal behind, without reading it."""
    return (ontology_root / RELEASE_JOURNAL_FILENAME).is_file()


def complete_pending_release(ontology_root: Path) -> bool:
    """Re-apply a journaled release left behind by a crashed materialization.

    Idempotent: safe to call whether or not any real file already reflects
    the journaled content (each file is re-written with `os.replace`
    regardless). Returns `True` if a journal was found and healed, `False`
    if there was nothing to do (the common case: near-zero overhead, a
    single `Path.is_file` check).

    Never trusts the journal blindly: a corrupt-JSON or structurally
    malformed journal (including a path-traversal file key) is quarantined
    to `<ontology_root>/.pending-release.json.rejected` and a clear
    `ValidationError` is raised, instead of crashing every future
    container start-up on the same journal. A well-formed journal is applied
    to a shadow copy of the tree and re-validated with `validate_ontology`
    before touching a single real file; if the resulting tree would be
    invalid the journal is quarantined the same way and the real tree is
    left untouched.
    """
    journal_path = ontology_root / RELEASE_JOURNAL_FILENAME
    if not journal_path.is_file():
        return False
    try:
        payload = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        quarantine_path = _quarantine_journal(journal_path)
        raise ValidationError(
            f"ontology release journal at {journal_path} is corrupt and was "
            f"quarantined to {quarantine_path} without modifying the ontology "
            f"tree; inspect and discard it before releasing again: {exc}"
        ) from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        quarantine_path = _quarantine_journal(journal_path)
        raise ValidationError(
            f"ontology release journal at {journal_path} is malformed and was "
            f"quarantined to {quarantine_path} without modifying the ontology tree"
        )
    root_resolved = ontology_root.resolve()
    edits: dict[Path, str] = {}
    for relative, new_text in files.items():
        if not isinstance(relative, str) or not isinstance(new_text, str):
            quarantine_path = _quarantine_journal(journal_path)
            raise ValidationError(
                f"ontology release journal at {journal_path} is malformed and was "
                f"quarantined to {quarantine_path} without modifying the ontology tree"
            )
        real_path = (ontology_root / relative).resolve()
        if not real_path.is_relative_to(root_resolved):
            quarantine_path = _quarantine_journal(journal_path)
            raise ValidationError(
                f"ontology release journal at {journal_path} is malformed and was "
                f"quarantined to {quarantine_path} without modifying the ontology tree"
            )
        edits[real_path] = new_text

    domain_profile = _resolve_journal_domain_profile(
        ontology_root, payload.get("release") if isinstance(payload, dict) else None
    )
    with tempfile.TemporaryDirectory(prefix="kip-ontology-heal-") as tmp:
        shadow_root = Path(tmp) / "ontology"
        shutil.copytree(ontology_root, shadow_root)
        for real_path, new_text in edits.items():
            shadow_path = shadow_root / real_path.relative_to(ontology_root)
            shadow_path.parent.mkdir(parents=True, exist_ok=True)
            shadow_path.write_text(new_text, encoding="utf-8")
        errors = validate_ontology(shadow_root, domain_profile=domain_profile)
    if errors:
        quarantine_path = _quarantine_journal(journal_path)
        raise ValidationError(
            f"ontology release journal at {journal_path} would produce an "
            f"invalid ontology tree and was quarantined to {quarantine_path} "
            "without modifying the ontology tree: " + "; ".join(errors)
        )

    for real_path, new_text in edits.items():
        _atomic_write(real_path, new_text)
    journal_path.unlink()
    return True


def _quarantine_journal(journal_path: Path) -> Path:
    """Rename a rejected journal aside so it is never re-applied automatically.

    Overwrites any previously quarantined journal at the same path: only the
    most recent rejection matters for diagnosing why healing keeps failing,
    and leaving a stale `.rejected` file around would not add information.
    """
    quarantine_path = journal_path.with_name(
        journal_path.name + REJECTED_RELEASE_JOURNAL_SUFFIX
    )
    os.replace(journal_path, quarantine_path)
    return quarantine_path


def _resolve_journal_domain_profile(ontology_root: Path, release_info: object) -> str:
    """Best-effort domain profile to shadow-validate a journal's resulting tree.

    Predicate/entity-type releases record the real `domain_profile` used at
    materialization time under `release.domain_profile` (see
    `_apply_via_shadow`). Older journals (or a hand-written one, as in
    crash-recovery tests) may omit it; fall back to any domain profile
    shipped on disk, the same fallback `_resolve_predicate_shadow_profile`
    uses when the caller's profile does not exist.
    """
    if isinstance(release_info, dict):
        candidate = release_info.get("domain_profile")
        if isinstance(candidate, str):
            try:
                domain_profile_path(ontology_root, candidate)
            except ValidationError:
                pass
            else:
                return candidate
    return _any_domain_profile(ontology_root)


def complete_pending_release_locked(ontology_root: Path) -> bool:
    """Like `complete_pending_release`, but acquires the release lock first.

    For callers outside `materialize_ontology_release`'s own lock scope
    (currently only `kip.container.build_container` at process start-up).
    """
    with _release_lock(ontology_root):
        return complete_pending_release(ontology_root)


def _write_release_journal(
    ontology_root: Path,
    edits: dict[Path, str],
    release_info: dict[str, str],
) -> None:
    journal_path = ontology_root / RELEASE_JOURNAL_FILENAME
    payload = {
        "release": release_info,
        "files": {
            str(real_path.relative_to(ontology_root)): new_text
            for real_path, new_text in edits.items()
        },
    }
    fd, tmp_name = tempfile.mkstemp(
        dir=str(ontology_root), prefix=f".{RELEASE_JOURNAL_FILENAME}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, journal_path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    dir_fd = os.open(str(ontology_root), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _clear_release_journal(ontology_root: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        (ontology_root / RELEASE_JOURNAL_FILENAME).unlink()


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

    if candidate.symbol in existing_entity_types:
        # A retry of the *same* candidate must stay idempotent, but
        # approving a *different* candidate that reuses an already-released
        # symbol must not silently no-op without writing its content.
        _check_idempotent_or_conflict(
            "entity type",
            candidate.symbol,
            existing_entity_types[candidate.symbol],
            _entity_type_fields(parent, candidate.label, candidate.definition),
        )
        return _release("entity_type", candidate.symbol, relative_path, current_version)

    block = _entity_type_block(candidate.symbol, parent, candidate.label, candidate.definition)
    new_version = _bump_minor(current_version)
    new_text = _set_version_line(text, new_version)
    new_text = _insert_top_level_block(new_text, "entity_types", block)

    _apply_via_shadow(
        ontology_root,
        domain_profile,
        {domain_path: new_text},
        release_info={
            "kind": "entity_type",
            "symbol": candidate.symbol,
            "version": new_version,
            "domain_profile": domain_profile,
        },
    )
    return _release("entity_type", candidate.symbol, relative_path, new_version)


def _materialize_predicate(
    ontology_root: Path,
    domain_profile: str,
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

    domain = candidate.domain or [_DEFAULT_PREDICATE_ROOT]
    range_ = candidate.range or [_DEFAULT_PREDICATE_ROOT]
    inverse = candidate.inverse
    risk = candidate.risk or _DEFAULT_PREDICATE_RISK
    review = candidate.review or _DEFAULT_PREDICATE_REVIEW
    extraction = candidate.extraction or _DEFAULT_PREDICATE_EXTRACTION

    if candidate.symbol in existing_predicates:
        _check_idempotent_or_conflict(
            "predicate",
            candidate.symbol,
            existing_predicates[candidate.symbol],
            _predicate_fields(
                label=candidate.label,
                definition=candidate.definition,
                domain=domain,
                range_=range_,
                inverse=inverse,
                risk=risk,
                review=review,
                extraction=extraction,
            ),
        )
        return _release("predicate", candidate.symbol, relative_path, current_version)

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

    domain_profile_for_shadow = _resolve_predicate_shadow_profile(ontology_root, domain_profile)
    _apply_via_shadow(
        ontology_root,
        domain_profile_for_shadow,
        edits,
        release_info={
            "kind": "predicate",
            "symbol": candidate.symbol,
            "version": new_version,
            "domain_profile": domain_profile_for_shadow,
        },
    )
    return _release("predicate", candidate.symbol, relative_path, new_version)


def _resolve_predicate_shadow_profile(ontology_root: Path, domain_profile: str) -> str:
    """Prefer the caller's real domain profile for predicate shadow validation.

    Predicate releases do not touch a domain profile file, but
    `validate_ontology` needs one to validate the tree as a whole. Using the
    real `domain_profile` a predicate's domain/range can cite domain-profile
    entity types (e.g. `ResearchProject`) and still validate; falling back to
    an arbitrary profile (alphabetically first, which may be an empty
    placeholder) would make such a predicate impossible to approve. Only
    fall back to `_any_domain_profile` when the passed profile does not name
    a file that actually exists on disk.
    """
    try:
        domain_profile_path(ontology_root, domain_profile)
    except ValidationError:
        return _any_domain_profile(ontology_root)
    return domain_profile


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
    *,
    release_info: dict[str, str],
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
    # A multi-file release (predicate + review-policy sync) is not
    # group-atomic across two `os.replace` calls: journal the full new
    # contents first (fsynced) so a crash between the replaces can be healed
    # by `complete_pending_release` instead of bricking every subsequent
    # `OntologyCatalog.load` with a tree that violates the exact-match
    # invariant `validate_ontology` enforces.
    _write_release_journal(ontology_root, edits, release_info)
    for real_path, new_text in edits.items():
        _atomic_write(real_path, new_text)
    _clear_release_journal(ontology_root)


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
    major, minor = match.group(1), match.group(2)
    return f"{major}.{int(minor) + 1}.0"


def _set_version_line(text: str, new_version: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = _VERSION_LINE_RE.fullmatch(line.rstrip("\n"))
        if match is not None:
            # Preserve a trailing `# comment` on the version line if there
            # was one; dropping it would be acceptable too, but keeping it
            # is cheap and avoids a spurious diff on every release.
            comment = match.group("comment")
            suffix = f"  {comment}" if comment else ""
            lines[index] = f"version: {new_version}{suffix}"
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


def _entity_type_fields(parent: str | None, label: str, definition: str) -> dict[str, object]:
    """The semantic fields the materializer writes for an entity type symbol.

    Mirrors `_entity_type_block`'s shape (a `parent` key present only when
    a parent is set) so it can be compared field-by-field against the
    already-parsed YAML mapping for an already-released symbol.
    """
    fields: dict[str, object] = {}
    if parent is not None:
        fields["parent"] = parent
    fields["label_ko"] = label
    fields["description_ko"] = definition
    return fields


def _predicate_fields(
    *,
    label: str,
    definition: str,
    domain: list[str],
    range_: list[str],
    inverse: str | None,
    risk: str,
    review: str,
    extraction: str,
) -> dict[str, object]:
    """The semantic fields the materializer writes for a predicate symbol."""
    return {
        "description": definition,
        "label_ko": label,
        "domain": domain,
        "range": range_,
        "inverse": inverse,
        "risk": risk,
        "extraction": extraction,
        "review": review,
    }


def _check_idempotent_or_conflict(
    kind_label: str,
    symbol: str,
    existing: object,
    would_be: dict[str, object],
) -> None:
    """Approving an already-released symbol is idempotent only on a match.

    A retry of the *same* candidate must be a silent no-op (needed so an
    approval that already landed can be safely retried). Approving a
    *different* candidate that happens to reuse an already-released symbol
    must not also silently no-op without writing its content: compare the
    fields the materializer would write against what is already on disk
    (nothing stricter than that) and raise `ConflictError` on any
    divergence.
    """
    if not isinstance(existing, dict) or any(
        existing.get(field) != value for field, value in would_be.items()
    ):
        raise ConflictError(
            f"{kind_label} {symbol!r} already released with different content"
        )
