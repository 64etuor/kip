"""`scripts/changelog-section.sh` against this repository's real CHANGELOG.md.

The publish workflow puts the extracted section at the top of the GitHub
release body and fails the tag when the version has none, so an extraction bug
either publishes the wrong notes or blocks a release. These cases run the
shipped script, not a reimplementation of it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "changelog-section.sh"
CHANGELOG = ROOT / "CHANGELOG.md"
HEADING = re.compile(r"^## (\d+\.\d+\.\d+)(?: |$)", re.MULTILINE)


def _extract(version: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), version],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )


def _released_versions() -> list[str]:
    return HEADING.findall(CHANGELOG.read_text(encoding="utf-8"))


# Resolved inside the test, not at collection: finding the longest section
# runs the script once per released version, and collection repeats in every
# xdist worker whether or not that worker runs this case.
_SELECTORS = {
    "newest": lambda versions: versions[0],
    "longest": lambda versions: max(versions, key=lambda version: len(_extract(version).stdout)),
    "oldest": lambda versions: versions[-1],
}


@pytest.mark.parametrize("which", list(_SELECTORS))
def test_section_is_that_version_and_nothing_after_it(which: str) -> None:
    version = _SELECTORS[which](_released_versions())
    result = _extract(version)

    assert result.returncode == 0, result.stderr
    section = result.stdout
    assert section.strip(), f"the {version} section came back empty"
    # The section stops at the next `## ` heading and carries no heading itself.
    assert not re.search(r"^## ", section, re.MULTILINE), section[:200]
    # Leading and trailing blank lines are trimmed.
    assert section == section.lstrip("\n")
    assert not section.endswith("\n\n")
    # It really is this version's text: the line that follows the heading in
    # the file is the first line of the extraction.
    body = CHANGELOG.read_text(encoding="utf-8")
    start = body.index(f"\n## {version}")
    first_body_line = body[start:].split("\n", 2)[2].lstrip("\n").split("\n", 1)[0]
    assert section.split("\n", 1)[0] == first_body_line


def test_a_version_with_no_section_fails_rather_than_returning_nothing() -> None:
    result = _extract("99.99.99")

    assert result.returncode == 1
    assert not result.stdout.strip()
    assert 'has no "## 99.99.99" section' in result.stderr


def test_the_heading_matches_on_the_version_field_alone() -> None:
    # `## 3.14.1 - 2026-09-13`: the date is never predicted, so a section is
    # found by its version whatever date follows it.
    newest = _released_versions()[0]
    heading = next(
        line for line in CHANGELOG.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"## {newest}")
    )
    assert heading != f"## {newest}", "this case needs a heading that carries a date"
    assert _extract(newest).returncode == 0


def test_an_oversized_section_is_refused_before_the_release_is_published() -> None:
    changelog = ROOT / "CHANGELOG.md"
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT), _released_versions()[0], "--file", str(changelog)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "KIP_CHANGELOG_MAX_SECTION_BYTES": "10"},
    )

    assert result.returncode == 1
    assert "over the 10 character limit" in result.stderr
