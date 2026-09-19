from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from kip.documentation import documentation_link_errors
from scripts.verify_project import (
    ROOT,
    ROOT_COMMIT,
    _repository_link_errors,
    _skill_files,
    _tracked_files,
)


def test_skill_files_ignore_macos_metadata(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("content", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"metadata")

    assert _skill_files(tmp_path) == {Path("SKILL.md"): b"content"}


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        check=True, capture_output=True,
    )


def test_tracked_files_is_none_outside_a_checkout(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("[x](b.md)\n", encoding="utf-8")

    assert _tracked_files(tmp_path) is None


@needs_git
def test_repository_pass_catches_links_in_unshipped_documents(tmp_path: Path) -> None:
    (tmp_path / "docs/plans").mkdir(parents=True)
    (tmp_path / "docs/plans/hist.md").write_text("# Plan\n[gone](nope.md)\n[kept](정산.md)\n", encoding="utf-8")
    (tmp_path / "docs/plans/정산.md").write_text("# Korean name\n", encoding="utf-8")
    (tmp_path / "docs/plans/deleted.md").write_text("tracked then removed\n", encoding="utf-8")
    (tmp_path / "untracked.md").write_text("never added\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "docs")
    _git(tmp_path, "commit", "-q", "-m", "plans")
    (tmp_path / "docs/plans/deleted.md").unlink()

    tracked = _tracked_files(tmp_path)

    assert tracked is not None
    assert set(tracked) == {"docs/plans/hist.md", "docs/plans/정산.md"}
    assert documentation_link_errors(tracked, scope="repository") == [
        "docs/plans/hist.md:2: repository link target missing: nope.md"
    ]


@needs_git
def test_git_failure_inside_a_checkout_is_an_error_not_a_skip(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()  # looks like a checkout, is not one

    with pytest.raises(subprocess.CalledProcessError):
        _tracked_files(tmp_path)
    errors = _repository_link_errors(tmp_path, [])
    assert len(errors) == 1
    assert errors[0].startswith("git ls-files failed, repository link check did not run: ")


@needs_git
def test_empty_listing_inside_a_checkout_is_an_error_not_a_skip(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "docs.md").write_text("[x](gone.md)\n", encoding="utf-8")  # never added

    assert _repository_link_errors(tmp_path, []) == [
        "git ls-files listed no tracked files, repository link check did not run"
    ]


def test_outside_a_checkout_the_repository_pass_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "docs.md").write_text("[x](gone.md)\n", encoding="utf-8")

    assert _repository_link_errors(tmp_path, []) == []


@needs_git
def test_repository_pass_reports_a_shipped_defect_once(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/shipped.md").write_text("[x](gone.md)\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "docs")
    _git(tmp_path, "commit", "-q", "-m", "docs")
    packaged = ["docs/shipped.md:1: packaged link target missing: gone.md"]

    assert _repository_link_errors(tmp_path, packaged) == []
    assert _repository_link_errors(tmp_path, []) == [
        "docs/shipped.md:1: repository link target missing: gone.md"
    ]


@needs_git
def test_pinned_github_links_are_verified_against_history(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/record.md").write_text("# kept\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "docs")
    _git(tmp_path, "commit", "-q", "-m", "record")
    revision = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    bogus = "0" * 40
    (tmp_path / "docs/status.md").write_text(
        f"[ok](https://github.com/64etuor/kip/blob/{revision}/docs/record.md)\n"
        f"[bad path](https://github.com/64etuor/kip/blob/{revision}/docs/missing.md)\n"
        f"[bad rev](https://github.com/64etuor/kip/blob/{bogus}/docs/record.md)\n"
        "[other repo](https://github.com/example/other/blob/"
        f"{bogus}/README.md)\n"
        f"See https://github.com/64etuor/kip/blob/{revision}/docs/record.md.\n"
        f"[dir](https://github.com/64etuor/kip/tree/{revision}/docs)\n"
        f"[no dir](https://github.com/64etuor/kip/tree/{revision}/docs/absent-dir)\n"
        "```\n"
        f"https://github.com/64etuor/kip/blob/{revision}/docs/example-only.md\n"
        "```\n"
        f"[empty](https://github.com/64etuor/kip/blob/{revision}/.)\n"
        f"[escape](https://github.com/64etuor/kip/blob/{revision}/docs/../../outside.md)\n"
        f"**https://github.com/64etuor/kip/blob/{revision}/docs/record.md**\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "docs")
    _git(tmp_path, "commit", "-q", "-m", "status")

    assert _repository_link_errors(tmp_path, [], anchor=revision) == [
        f"docs/status.md:2: pinned link target missing in history: {revision[:12]}:docs/missing.md",
        f"docs/status.md:3: pinned revision is not in this repository's history: {bogus[:12]}",
        f"docs/status.md:7: pinned link target missing in history: {revision[:12]}:docs/absent-dir",
        "docs/status.md:11: pinned link path is not valid: ''",
        "docs/status.md:12: pinned link path is not valid: 'docs/../../outside.md'",
    ]
    # A repository without the anchor commit is a recipient's own repository,
    # not a KIP checkout: pinned links are left alone, relative links still count.
    assert _repository_link_errors(tmp_path, [], anchor="0" * 40) == []


@needs_git
def test_shallow_clone_fails_once_by_name(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    (origin / "docs").mkdir(parents=True)
    (origin / "docs/old.md").write_text("# old\n", encoding="utf-8")
    _git(origin, "init", "-q")
    _git(origin, "add", "docs")
    _git(origin, "commit", "-q", "-m", "old")
    root_commit = subprocess.run(
        ["git", "-C", str(origin), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    (origin / "docs/new.md").write_text(
        f"[old](https://github.com/64etuor/kip/blob/{root_commit}/docs/old.md)\n", encoding="utf-8"
    )
    _git(origin, "add", "docs")
    _git(origin, "commit", "-q", "-m", "new")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(shallow)], check=True, capture_output=True
    )

    errors = _repository_link_errors(shallow, [], anchor=root_commit)

    assert len(errors) == 1
    assert errors[0].startswith("this clone is shallow, so the commit-pinned links cannot be verified")


def test_root_commit_constant_matches_this_checkout() -> None:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("needs git and this checkout; a package archive has neither")
    roots = subprocess.run(
        ["git", "-C", str(ROOT), "rev-list", "--max-parents=0", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.split()

    # If this fails after a history rewrite, ROOT_COMMIT must follow it; otherwise
    # the pinned-link check silently treats the checkout as someone else's repository.
    assert ROOT_COMMIT in roots


def test_every_repository_document_link_resolves() -> None:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("needs git and this checkout; a package archive has neither")
    tracked = _tracked_files(ROOT)

    assert tracked is not None, "the repository link check must run inside the checkout"
    assert documentation_link_errors(tracked, scope="repository") == []
    assert _repository_link_errors(ROOT, []) == []
