from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kip.domain.models import ArtifactView
from kip.errors import NotFoundError


@dataclass(frozen=True, slots=True)
class FilesystemSourceGrant:
    name: str
    root: Path
    snapshot_id: str


@dataclass(frozen=True, slots=True)
class FilesystemAccessPolicy:
    """Deployment-owned authorization, independent of request ACL grants.

    An empty policy denies filesystem evidence. Repository constructors may
    accept None for standalone use; composed applications always install a
    policy. Configured roots are resolved once, when configuration is loaded.
    """

    grants: tuple[FilesystemSourceGrant, ...]
    follow_symlinks: bool = False

    def payload(self) -> list[dict[str, str]]:
        return [
            {"name": grant.name, "root": str(grant.root), "snapshot_id": grant.snapshot_id}
            for grant in self.grants
        ]

    def allows_artifact(self, view: ArtifactView) -> bool:
        source = view.source_object
        if source is None:
            return False
        if source.system_kind != "filesystem":
            return True
        snapshot = source.acl_snapshot
        path = view.artifact.source_path
        if snapshot is None or path is None:
            return False
        return any(
            grant.name == source.system_name
            and grant.snapshot_id == snapshot.id
            and _stored_descendant(Path(path), grant.root)
            for grant in self.grants
        )

    def require_path(self, path: Path, *, source_name: str | None = None) -> Path:
        """Check live path resolution before any source bytes are opened."""
        try:
            resolved = path.resolve()
            for grant in self.grants:
                if source_name is not None and grant.name != source_name:
                    continue
                if not _stored_descendant(path, grant.root):
                    continue
                if not _stored_descendant(resolved, grant.root):
                    continue
                if not self.follow_symlinks:
                    current = path
                    while current != grant.root:
                        if current.is_symlink():
                            break
                        current = current.parent
                    else:
                        # Also reject replacement of a configured root itself.
                        if not grant.root.is_symlink():
                            return resolved
                    continue
                return resolved
        except (OSError, RuntimeError):
            pass
        raise NotFoundError("source file is outside the configured access scope")


def _stored_descendant(path: Path, root: Path) -> bool:
    # Stored paths must already be normalized by ingestion. Do not resolve
    # the live filesystem during ranking, which must work for offline sources.
    return path.is_absolute() and ".." not in path.parts and root in path.parents
