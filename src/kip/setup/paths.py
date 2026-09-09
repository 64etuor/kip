from __future__ import annotations

from pathlib import Path, PurePosixPath


def validate_container_source_target(value: str) -> None:
    """Protect container runtime paths without applying host symlink rules."""
    target = PurePosixPath(value)
    protected = (
        "/app", "/opt", "/usr", "/etc", "/proc", "/sys", "/dev", "/run",
        "/bin", "/sbin", "/lib", "/lib64", "/var/lib/kip", "/data",
    )
    if not target.is_absolute() or ".." in target.parts or target == PurePosixPath("/tmp"):
        raise ValueError("source target must be a dedicated absolute directory outside container runtime paths")
    for value in protected:
        reserved = PurePosixPath(value)
        if target == reserved or target in reserved.parents or reserved in target.parents:
            raise ValueError(f"source target overlaps a protected container runtime path: {reserved}")


def canonical_source_root(value: str, *, project_root: Path) -> Path:
    supplied = Path(value).expanduser()
    if supplied.is_symlink():
        raise ValueError("source root symlinks are not allowed")
    root = supplied.resolve()
    project = project_root.expanduser().resolve()
    home = Path.home().resolve()
    if (
        root == Path("/")
        or root in (home, project)
        or project.is_relative_to(root)
    ):
        raise ValueError(f"source root is too broad: {root}")
    if not root.is_dir():
        raise ValueError(f"source root is not an existing directory: {root}")
    validate_container_source_target(str(root))
    return root


def canonical_managed_path(
    value: str,
    *,
    project_root: Path,
    source_roots: list[Path],
    other_managed_paths: list[Path] | None = None,
) -> Path:
    supplied = Path(value).expanduser()
    if supplied.is_symlink():
        raise ValueError("managed storage path symlinks are not allowed")
    path = supplied.resolve()
    project = project_root.expanduser().resolve()
    home = Path.home().resolve()
    if (
        path == Path("/")
        or path in (home, project)
        or project.is_relative_to(path)
    ):
        raise ValueError(f"managed storage path is too broad: {path}")
    for source_root in source_roots:
        if _overlaps(path, source_root):
            raise ValueError(
                f"managed storage path overlaps a read-only source: {path}"
            )
    for other in other_managed_paths or []:
        if _overlaps(path, other):
            raise ValueError(f"managed storage paths overlap: {path} and {other}")
    return path


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)
