from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from kip.adapters.connectors.filesystem import FileSystemConnector
from kip.adapters.storage.cloud_files import is_cloud_placeholder


@pytest.mark.parametrize("metadata", [
    {"st_flags": 0x40000000},
    {"st_file_attributes": 0x1000},
    {"st_file_attributes": 0x40000},
    {"st_file_attributes": 0x400000},
])
def test_cloud_placeholder_uses_os_metadata(metadata):
    assert is_cloud_placeholder(SimpleNamespace(**metadata))


def test_sparse_file_is_not_assumed_to_be_a_cloud_placeholder():
    assert not is_cloud_placeholder(SimpleNamespace(st_size=8192, st_blocks=0))
    assert not is_cloud_placeholder(SimpleNamespace(st_flags=0, st_file_attributes=0))


def test_cloud_file_is_skipped_before_hash_or_parser(tmp_path, monkeypatch):
    placeholder = tmp_path / "online.txt"
    placeholder.write_text("unavailable contents")
    original_stat = Path.stat

    def cloud_stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == placeholder:
            return SimpleNamespace(st_flags=0x40000000, st_mode=value.st_mode,
                                   st_size=value.st_size, st_mtime_ns=value.st_mtime_ns)
        return value

    monkeypatch.setattr(Path, "stat", cloud_stat)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("hydrated file"))
    connector = FileSystemConnector(tmp_path, settle_seconds=0)
    assert list(connector.scan()) == []
    assert connector.skipped_present_relative_paths == {"online.txt"}
    assert connector.skipped_reason_counts == {"cloud_placeholder": 1}


def test_followlinks_prunes_escape_before_descent_and_stops_cycles(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "local.txt").write_text("local")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "loop").symlink_to(root, target_is_directory=True)
    original_scandir = os.scandir

    def bounded_scandir(path):
        assert Path(path).resolve().is_relative_to(root), "descended outside source"
        assert Path(path).name != "loop", "descended into cycle"
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", bounded_scandir)
    connector = FileSystemConnector(root, follow_symlinks=True, settle_seconds=0)
    assert [record.relative_path for record in connector.scan()] == ["local.txt"]
    assert connector.deferred_relative_prefixes == {"escape", "loop"}
    assert connector.skipped_reason_counts == {"directory_outside_root": 1, "directory_cycle": 1}


def test_filtered_files_have_bounded_reason_counts(tmp_path):
    for index in range(100):
        (tmp_path / f"{index}.bin").touch()
    connector = FileSystemConnector(tmp_path, include_extensions={".txt"})
    assert list(connector.scan()) == []
    assert connector.skipped_reason_counts == {"extension_filter": 100}
    assert len(connector.skipped_present_relative_paths) == 100


def test_directory_alias_cycle_is_pruned(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "to-b").symlink_to(tmp_path / "b", target_is_directory=True)
    (tmp_path / "b" / "to-a").symlink_to(tmp_path / "a", target_is_directory=True)
    connector = FileSystemConnector(tmp_path, follow_symlinks=True, settle_seconds=0)
    assert list(connector.scan()) == []
    assert connector.deferred_relative_prefixes == {"a/to-b/to-a", "b/to-a/to-b"}


def test_partial_scan_never_publishes_diagnostics(tmp_path):
    (tmp_path / "eligible.txt").touch()
    (tmp_path / "skipped.bin").touch()
    connector = FileSystemConnector(tmp_path, include_extensions={".txt"}, settle_seconds=0)
    assert len(list(connector.scan())) == 1
    assert connector.skipped_reason_counts == {"extension_filter": 1}
    iterator = connector.scan()
    next(iterator)
    iterator.close()
    assert connector.skipped_reason_counts == {}
    assert connector.skipped_present_relative_paths == frozenset()
    assert connector.deferred_relative_prefixes == frozenset()


def test_sync_cloud_placeholder_preserves_prior_extraction(test_container, monkeypatch):
    root = test_container.settings.project_root / "source"
    target = root / "online.txt"
    target.write_text("existing evidence")
    context = test_container.application.operations.request_context()
    ingestion = test_container.application.ingestion
    assert ingestion.sync_filesystem(context, "fixture").inserted == 1
    original_revisions = dict(test_container.repository.state.current_revision_by_object)
    original_stat = Path.stat

    def cloud_stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == target:
            return SimpleNamespace(st_flags=0x40000000, st_mode=value.st_mode,
                                   st_size=value.st_size, st_mtime_ns=value.st_mtime_ns)
        return value

    monkeypatch.setattr(Path, "stat", cloud_stat)
    monkeypatch.setattr(ingestion, "ingest_file", lambda *args, **kwargs: pytest.fail("parsed placeholder"))
    for _ in range(3):
        summary = ingestion.sync_filesystem(context, "fixture")
        assert summary.scanned == summary.absent == summary.tombstoned == 0
        assert summary.warnings == [
            "present but skipped from ingestion: cloud_placeholder=1; prior revisions, if any, stay active"
        ]
    assert test_container.repository.state.current_revision_by_object == original_revisions
    assert test_container.repository.state.absent_scan_counts == {}


def test_unvisited_directory_blocks_deletion_reconciliation(test_container):
    root = test_container.settings.project_root / "source"
    directory = root / "was-local"
    directory.mkdir()
    (directory / "existing.txt").write_text("existing evidence")
    (root / "keeper.txt").write_text("keeper")
    context = test_container.application.operations.request_context()
    ingestion = test_container.application.ingestion
    assert ingestion.sync_filesystem(context, "fixture").inserted == 2
    original_revisions = dict(test_container.repository.state.current_revision_by_object)
    moved = root.parent / "moved"
    directory.rename(moved)
    directory.symlink_to(moved, target_is_directory=True)
    for _ in range(3):
        summary = ingestion.sync_filesystem(context, "fixture")
        assert summary.absent == summary.tombstoned == 0
        assert any("scan deferred 1 directories" in warning for warning in summary.warnings)
    assert test_container.repository.state.current_revision_by_object == original_revisions
    assert test_container.repository.state.absent_scan_counts == {}


def test_sync_filter_warning_count_is_independent_of_corpus_size(test_container):
    root = test_container.settings.project_root / "source"
    for index in range(100):
        (root / f"{index}.bin").touch()
    context = test_container.application.operations.request_context()
    summary = test_container.application.ingestion.sync_filesystem(context, "fixture", dry_run=True)
    assert summary.scanned == 0
    assert summary.warnings == [
        "present but skipped from ingestion: extension_filter=100; prior revisions, if any, stay active"
    ]


def test_failed_file_diagnostics_are_bounded(test_container, monkeypatch):
    root = test_container.settings.project_root / "source"
    for index in range(60):
        (root / f"{index}.txt").touch()
    ingestion = test_container.application.ingestion

    def fail(*args, **kwargs):
        raise OSError("failure " * 500)

    monkeypatch.setattr(ingestion, "ingest_file", fail)
    context = test_container.application.operations.request_context()
    summary = ingestion.sync_filesystem(context, "fixture")
    assert summary.failed == 60
    assert len(summary.warnings) == 51
    assert max(map(len, summary.warnings)) <= 1000
    assert summary.warnings[-1] == "10 additional file failures; details limited to the first 50 files"


@pytest.mark.parametrize("root_placeholder", [False, True])
def test_cloud_marked_directory_preserves_locally_resident_children(tmp_path, monkeypatch, root_placeholder):
    directory = tmp_path if root_placeholder else tmp_path / "online-folder"
    directory.mkdir(exist_ok=True)
    (directory / "local.txt").write_text("locally available")
    original_stat = Path.stat

    def cloud_stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == directory:
            return SimpleNamespace(st_flags=0x40000000, st_mode=value.st_mode)
        return value

    monkeypatch.setattr(Path, "stat", cloud_stat)
    connector = FileSystemConnector(tmp_path, settle_seconds=0)
    records = list(connector.scan())
    assert len(records) == 1
    assert records[0].path == directory / "local.txt"
    assert connector.skipped_reason_counts == {}
    assert connector.deferred_relative_prefixes == frozenset()


def test_non_regular_document_path_is_skipped_before_open(tmp_path, monkeypatch):
    pipe = tmp_path / "document.pdf"
    os.mkfifo(pipe)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("opened FIFO"))
    connector = FileSystemConnector(tmp_path, settle_seconds=0)
    assert list(connector.scan()) == []
    assert connector.skipped_present_relative_paths == {"document.pdf"}
    assert connector.skipped_reason_counts == {"non_regular": 1}
