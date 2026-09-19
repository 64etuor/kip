import unicodedata
from pathlib import Path

from kip.documentation import documentation_link_errors
from kip.package_archive_policy import selected_source_files


def test_link_check_uses_recipient_files_not_the_checkout():
    files = {"README.md": b"[audit](docs/historical.md)"}
    assert documentation_link_errors(files) == ["README.md:1: packaged link target missing: docs/historical.md"]
    files["docs/historical.md"] = b"# Historical evidence"
    assert documentation_link_errors(files) == []


def test_links_handle_relative_paths_spaces_fragments_and_code_examples():
    files = {
        "docs/guide.md": b'[file](<../my report.md#result>)\n[web](https://example.test/missing)\n```md\n[example](missing.md)\n```\n',
        "my report.md": b"# Result",
    }
    assert documentation_link_errors(files) == []


def test_links_match_across_unicode_normalization_forms():
    nfd_name = unicodedata.normalize("NFD", "sample-data/정산.md")
    files = {"README.md": "[x](sample-data/정산.md)".encode(), nfd_name: b"# NFD on disk"}
    assert documentation_link_errors(files) == []


def test_two_files_differing_only_by_normalization_are_reported():
    nfd_name = unicodedata.normalize("NFD", "docs/정산.md")
    files = {"docs/정산.md": b"[a](missing-a.md)", nfd_name: b"[b](missing-b.md)"}
    assert documentation_link_errors(files) == [
        "docs/정산.md: two files differ only by Unicode normalization",
        "docs/정산.md:1: packaged link target missing: missing-a.md",
    ]


def test_scope_names_the_universe_in_the_message():
    files = {"docs/plans/a.md": b"[x](b.md)"}
    assert documentation_link_errors(files, scope="repository") == [
        "docs/plans/a.md:1: repository link target missing: b.md"
    ]


def test_all_shipped_document_links_resolve():
    root = Path(__file__).resolve().parents[1]
    assert documentation_link_errors({
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in selected_source_files(root)
    }) == []
