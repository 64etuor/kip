#!/usr/bin/env python3
"""Assert on `kip.envelope.v1` payloads captured from an installed deployment.

The end-to-end scripts in `scripts/e2e-*.sh` drive a real installation and
capture each command's stdout to a file; this module is the part that decides
whether the shipped artifact answered correctly. It is deliberately
stdlib-only and lives outside `src/` so that it can be run by whichever
`python3` is on PATH, against a deployment whose own environment must stay
untouched.

Every check fails loudly. `meta.warnings` is compared against an explicit
allowlist rather than ignored: a release that starts warning about something
new has changed observable behaviour and must say so on purpose.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "kip.envelope.v1"


class CheckFailed(Exception):
    """One assertion about a captured envelope did not hold."""


def _load(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise CheckFailed(f"{path}: cannot read captured envelope: {error}") from error
    if not text.strip():
        raise CheckFailed(f"{path}: captured envelope is empty")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise CheckFailed(f"{path}: stdout was not one JSON envelope: {error}") from error
    if not isinstance(payload, dict):
        raise CheckFailed(f"{path}: envelope must be a JSON object, got {type(payload).__name__}")
    return payload


def _require(condition: object, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def _envelope(path: Path, *, expect_ok: bool, allowed_warnings: frozenset[str]) -> dict[str, Any]:
    payload = _load(path)
    _require(
        payload.get("schema_version") == SCHEMA_VERSION,
        f"{path}: schema_version is {payload.get('schema_version')!r}, expected {SCHEMA_VERSION!r}",
    )
    _require(
        payload.get("ok") is expect_ok,
        f"{path}: ok is {payload.get('ok')!r}, expected {expect_ok!r} (error: {payload.get('error')!r})",
    )
    meta = payload.get("meta")
    _require(isinstance(meta, dict), f"{path}: envelope carries no meta object")
    assert isinstance(meta, dict)
    _require(meta.get("request_id"), f"{path}: meta.request_id is missing")
    _require(meta.get("workspace"), f"{path}: meta.workspace is missing")
    warnings = meta.get("warnings") or []
    _require(
        isinstance(warnings, list),
        f"{path}: meta.warnings is {type(warnings).__name__}, expected a list",
    )
    unexpected = sorted({str(warning) for warning in warnings} - set(allowed_warnings))
    _require(
        not unexpected,
        f"{path}: unexpected meta.warnings {unexpected}; this release expects "
        f"{sorted(allowed_warnings) or 'none'}. Either the deployment is degraded or the "
        "release changed its warnings without updating the end-to-end allowlist.",
    )
    if expect_ok:
        _require(payload.get("error") is None, f"{path}: ok envelope still carries error {payload.get('error')!r}")
    return payload


def _freshness(path: Path, label: str, data: dict[str, Any], *, allowed_verification: tuple[str, ...]) -> None:
    verification = data.get("source_verification")
    _require(
        verification in allowed_verification,
        f"{path}: {label}.source_verification is {verification!r}, expected one of {list(allowed_verification)}",
    )
    _require(
        "source_changed_since_index" in data,
        f"{path}: {label} carries no source_changed_since_index field",
    )
    changed = data["source_changed_since_index"]
    _require(
        changed is True or changed is False or changed is None,
        f"{path}: {label}.source_changed_since_index is {changed!r}; the field is three-valued "
        "(true / false / null) and must never be a string or a number",
    )
    # An e2e run reads a source the run itself just indexed and never touched.
    _require(
        changed is False,
        f"{path}: {label}.source_changed_since_index is {changed!r} for a source this run indexed "
        "and did not modify; null means the source could not be read at all",
    )
    _require(
        verification != "unavailable",
        f"{path}: {label}.source_verification is 'unavailable', so freshness was never established",
    )


def check_search(args: argparse.Namespace) -> None:
    path = Path(args.file)
    payload = _envelope(path, expect_ok=True, allowed_warnings=frozenset(args.allow_warning))
    hits = payload.get("data")
    _require(isinstance(hits, list), f"{path}: search data is {type(hits).__name__}, expected a list of hits")
    assert isinstance(hits, list)
    _require(
        len(hits) >= args.min_hits,
        f"{path}: search returned {len(hits)} hits, expected at least {args.min_hits}",
    )
    for index, hit in enumerate(hits):
        _require(isinstance(hit, dict), f"{path}: hit {index} is not an object")
        for field in ("unit_id", "artifact_id", "source_uri", "source_sha256", "title"):
            _require(hit.get(field), f"{path}: hit {index} has no {field}")
        locator = hit.get("locator")
        _require(isinstance(locator, dict), f"{path}: hit {index} has no locator object")
        assert isinstance(locator, dict)
        _require(locator.get("type"), f"{path}: hit {index} locator has no type")
        _require("data" in locator, f"{path}: hit {index} locator has no data")
        _require(
            hit.get("source_uri", "").startswith("file://"),
            f"{path}: hit {index} source_uri {hit.get('source_uri')!r} is not a filesystem locator",
        )
        _require(
            hit.get("evidence_role") == "discovery",
            f"{path}: hit {index} evidence_role is {hit.get('evidence_role')!r}, expected 'discovery'",
        )
        _require(
            hit.get("source_verification") == "not_checked",
            f"{path}: hit {index} source_verification is {hit.get('source_verification')!r}; a search "
            "preview never checks the live source",
        )
    if args.emit_shell:
        _emit_shell(path, hits, Path(args.emit_shell))
    print(f"search: {len(hits)} hits, every hit carries a locator and a source_uri")


def _emit_shell(path: Path, hits: list[dict[str, Any]], destination: Path) -> None:
    """Write the ids the shell needs to reopen this evidence, for `source`."""
    first = hits[0]
    workbook = next(
        (hit for hit in hits if isinstance(hit.get("locator"), dict) and hit["locator"].get("type") == "xlsx_sheet"),
        None,
    )
    _require(
        workbook is not None,
        f"{path}: no hit carries an xlsx_sheet locator, so xlsx-read cannot be exercised; "
        "the bundled sample workbook should have been indexed",
    )
    assert workbook is not None
    locator_data = workbook["locator"].get("data") or {}
    lines = [
        f"KIP_E2E_UNIT_ID={shlex.quote(str(first['unit_id']))}",
        f"KIP_E2E_XLSX_ARTIFACT_ID={shlex.quote(str(workbook['artifact_id']))}",
        f"KIP_E2E_XLSX_SHEET={shlex.quote(str(locator_data.get('sheet', '')))}",
        f"KIP_E2E_XLSX_RANGE={shlex.quote(str(locator_data.get('range', '')))}",
    ]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_read(args: argparse.Namespace) -> None:
    path = Path(args.file)
    payload = _envelope(path, expect_ok=True, allowed_warnings=frozenset(args.allow_warning))
    data = payload.get("data")
    _require(isinstance(data, dict), f"{path}: read data is {type(data).__name__}, expected an object")
    assert isinstance(data, dict)
    unit = data.get("unit")
    _require(isinstance(unit, dict), f"{path}: read carries no unit object")
    assert isinstance(unit, dict)
    if args.unit_id:
        _require(
            unit.get("id") == args.unit_id,
            f"{path}: read returned unit {unit.get('id')!r}, expected {args.unit_id!r}",
        )
    _require(unit.get("body"), f"{path}: read returned an empty body")
    _require(isinstance(unit.get("locator"), dict), f"{path}: read unit carries no locator")
    _require(data.get("source_uri"), f"{path}: read carries no source_uri")
    _require(data.get("indexed_source_sha256"), f"{path}: read carries no indexed_source_sha256")
    # `read` always re-hashes, so 'stat' is not a value it may report.
    _freshness(path, "read", data, allowed_verification=("sha256", "unavailable"))
    print(
        "read: source_verification="
        f"{data['source_verification']} source_changed_since_index={data['source_changed_since_index']!r}"
    )


def check_xlsx(args: argparse.Namespace) -> None:
    path = Path(args.file)
    payload = _envelope(path, expect_ok=True, allowed_warnings=frozenset(args.allow_warning))
    data = payload.get("data")
    _require(isinstance(data, dict), f"{path}: xlsx-read data is {type(data).__name__}, expected an object")
    assert isinstance(data, dict)
    _require(data.get("artifact_id"), f"{path}: xlsx-read carries no artifact_id")
    _require(data.get("source_uri"), f"{path}: xlsx-read carries no source_uri")
    _freshness(path, "xlsx-read", data, allowed_verification=("sha256",))
    rows = data.get("cells")
    _require(isinstance(rows, list) and rows, f"{path}: xlsx-read returned no cells")
    assert isinstance(rows, list)
    values: dict[str, Any] = {}
    for row in rows:
        _require(isinstance(row, list), f"{path}: xlsx-read cells must be a list of rows")
        for cell in row:
            _require(isinstance(cell, dict), f"{path}: xlsx-read cell is not an object")
            values[str(cell.get("coordinate"))] = cell.get("value")
    for expectation in args.expect_cell:
        coordinate, _, expected = expectation.partition("=")
        _require(
            coordinate in values,
            f"{path}: xlsx-read returned no cell {coordinate}; it returned {sorted(values)}",
        )
        actual = values[coordinate]
        _require(
            str(actual) == expected,
            f"{path}: xlsx-read cell {coordinate} is {actual!r}, expected {expected!r}. "
            "A spreadsheet number read through KIP must be the workbook's own value.",
        )
    print(f"xlsx-read: {len(values)} cells, source_verification={data['source_verification']}")


def check_error(args: argparse.Namespace) -> None:
    path = Path(args.file)
    payload = _envelope(path, expect_ok=False, allowed_warnings=frozenset(args.allow_warning))
    error = payload.get("error")
    _require(isinstance(error, dict), f"{path}: failing envelope carries no error object")
    assert isinstance(error, dict)
    _require(error.get("message"), f"{path}: failing envelope carries no error.message")
    if args.error_code:
        _require(
            error.get("code") == args.error_code,
            f"{path}: error.code is {error.get('code')!r}, expected {args.error_code!r}",
        )
    print(f"error envelope: code={error.get('code')}")


def check_version(args: argparse.Namespace) -> None:
    path = Path(args.file)
    payload = _envelope(path, expect_ok=True, allowed_warnings=frozenset(args.allow_warning))
    data = payload.get("data")
    _require(isinstance(data, dict), f"{path}: version data is {type(data).__name__}, expected an object")
    assert isinstance(data, dict)
    version = data.get("version")
    _require(
        version == args.expect,
        f"{path}: the installed deployment reports version {version!r}, expected {args.expect!r}. "
        "The artifact under test is not the build this run produced.",
    )
    print(f"version: {version}")


def check_capabilities(args: argparse.Namespace) -> None:
    path = Path(args.file)
    payload = _envelope(path, expect_ok=True, allowed_warnings=frozenset(args.allow_warning))
    data = payload.get("data")
    _require(isinstance(data, dict), f"{path}: capabilities data is {type(data).__name__}, expected an object")
    assert isinstance(data, dict)
    _require(
        data.get("repository") == args.repository,
        f"{path}: capabilities reports repository {data.get('repository')!r}, expected {args.repository!r}. "
        "A deployment that fell back to the in-memory repository loses every ingest at exit.",
    )
    _require(
        data.get("lexical_search") is True,
        f"{path}: capabilities reports lexical_search={data.get('lexical_search')!r}",
    )
    _require(
        data.get("semantic_search_configured") is args.expect_semantic,
        f"{path}: capabilities reports semantic_search_configured="
        f"{data.get('semantic_search_configured')!r}, expected {args.expect_semantic!r}",
    )
    print(
        "capabilities: repository="
        f"{data.get('repository')} semantic_search_configured={data.get('semantic_search_configured')!r}"
    )


def check_doctor(args: argparse.Namespace) -> None:
    path = Path(args.file)
    payload = _envelope(path, expect_ok=True, allowed_warnings=frozenset(args.allow_warning))
    data = payload.get("data")
    _require(isinstance(data, dict), f"{path}: doctor data is {type(data).__name__}, expected an object")
    assert isinstance(data, dict)
    checks = {item.get("name"): item for item in data.get("checks") or [] if isinstance(item, dict)}
    if args.deployment:
        # The configuration check names the config file the answering process
        # loaded, which is the one envelope field that says WHICH deployment
        # answered. A skill copy that resolved to another checkout fails here.
        config = (checks.get("configuration") or {}).get("details", {}).get("path")
        deployment = Path(args.deployment).resolve()
        _require(
            isinstance(config, str) and Path(config).resolve().is_relative_to(deployment),
            f"{path}: doctor loaded configuration {config!r}, which is not inside the deployment {deployment}",
        )
    for name, expected in [(name, True) for name in args.check_ok] + [(name, False) for name in args.check_not_ok]:
        check = checks.get(name)
        _require(check is not None, f"{path}: doctor reports no {name!r} check; it reports {sorted(checks)}")
        assert check is not None
        _require(
            check.get("ok") is expected,
            f"{path}: doctor check {name!r} is ok={check.get('ok')!r}, expected {expected!r}: {check.get('details')!r}",
        )
        if name == "skill_installs" and args.skill_installs is not None:
            installs = check.get("details", {}).get("installs") or []
            _require(
                len(installs) == args.skill_installs,
                f"{path}: skill_installs lists {len(installs)} skill copies, expected {args.skill_installs}: {installs!r}",
            )
    answered = f"answered from {args.deployment}; " if args.deployment else ""
    print(f"doctor: {answered}checks {sorted(args.check_ok)} ok, {sorted(args.check_not_ok)} not ok")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("file", help="File holding one captured envelope")
        subparser.add_argument(
            "--allow-warning",
            action="append",
            default=[],
            metavar="NAME",
            help="A meta.warnings entry this release expects; anything else fails",
        )

    search = subparsers.add_parser("search", help="Assert a search envelope")
    add_common(search)
    search.add_argument("--min-hits", type=int, default=1)
    search.add_argument("--emit-shell", metavar="FILE", help="Write the reopen ids as shell assignments")
    search.set_defaults(handler=check_search)

    read = subparsers.add_parser("read", help="Assert a read envelope")
    add_common(read)
    read.add_argument("--unit-id", default=None)
    read.set_defaults(handler=check_read)

    xlsx = subparsers.add_parser("xlsx", help="Assert an xlsx-read envelope")
    add_common(xlsx)
    xlsx.add_argument("--expect-cell", action="append", default=[], metavar="A1=VALUE")
    xlsx.set_defaults(handler=check_xlsx)

    error = subparsers.add_parser("error", help="Assert a failing envelope")
    add_common(error)
    error.add_argument("--error-code", default=None)
    error.set_defaults(handler=check_error)

    version = subparsers.add_parser("version", help="Assert a version envelope")
    add_common(version)
    version.add_argument("--expect", required=True, help="The version the installed deployment must report")
    version.set_defaults(handler=check_version)

    capabilities = subparsers.add_parser("capabilities", help="Assert a capabilities envelope")
    add_common(capabilities)
    capabilities.add_argument("--repository", default="postgresql")
    capabilities.add_argument(
        "--expect-semantic",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether this deployment is expected to have semantic search configured",
    )
    capabilities.set_defaults(handler=check_capabilities)

    doctor = subparsers.add_parser("doctor", help="Assert a doctor envelope")
    add_common(doctor)
    doctor.add_argument("--deployment", default=None, help="The loaded configuration must be inside this deployment")
    doctor.add_argument("--check-ok", action="append", default=[], metavar="NAME")
    doctor.add_argument("--check-not-ok", action="append", default=[], metavar="NAME")
    doctor.add_argument("--skill-installs", type=int, default=None, metavar="N", help="skill copies skill_installs must list")
    doctor.set_defaults(handler=check_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        arguments.handler(arguments)
    except CheckFailed as failure:
        print(f"e2e envelope check failed: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
