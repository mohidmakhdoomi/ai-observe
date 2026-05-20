"""Snapshot manifest capture and reconciliation for ai-observe.

This module is intentionally pure/deterministic enough to unit-test before the
observer wrapper wires it into command execution.  It does not launch child
processes or write sidecars; callers receive structured diagnostics suitable
for `<session>.meta.json`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import fnmatch
import hashlib
import os
from pathlib import Path
import stat
from typing import Any, Iterable


SCHEMA_VERSION = 2

BUILTIN_EXCLUDES = (
    ".git",
    "node_modules",
    "__pycache__",
    ".codev/observe/**",
    "**/*.pyc",
    "**/*.pyo",
    "**/*.swp",
    "**/*.swo",
    "**/*~",
    ".DS_Store",
    ".nfs*",
)


@dataclass(frozen=True)
class SnapshotDiagnostic:
    code: str
    message: str
    root: str | None = None
    path: str | None = None
    pattern: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    type: str
    mode: int
    mtime_ns: int
    ctime_ns: int
    root: str | None = None
    size: int | None = None
    dev: int | None = None
    ino: int | None = None
    symlink_target: str | None = None
    hash: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if k != "root" and v is not None}

    @property
    def object_identity(self) -> tuple[int, int] | None:
        if self.dev is None or self.ino is None:
            return None
        return (self.dev, self.ino)


@dataclass
class Manifest:
    entries: dict[str, ManifestEntry] = field(default_factory=dict)
    diagnostics: list[SnapshotDiagnostic] = field(default_factory=list)
    complete: bool = True

    def diagnostics_json(self) -> list[dict[str, Any]]:
        return [d.to_dict() for d in self.diagnostics]


def parse_exclude_patterns(value: str | None) -> list[str]:
    """Parse user excludes from colon- or newline-separated env text."""
    if not value:
        return []
    patterns: list[str] = []
    for line in value.splitlines():
        for part in line.split(os.pathsep):
            item = part.strip()
            if item:
                patterns.append(_normalize_pattern(item))
    return patterns


def all_exclude_patterns(user_value: str | None = None) -> list[str]:
    return [_normalize_pattern(p) for p in BUILTIN_EXCLUDES] + parse_exclude_patterns(user_value)


def should_exclude(relative_path: str | os.PathLike[str], patterns: Iterable[str]) -> bool:
    """Return whether a root-relative path matches an exclude pattern.

    Pattern semantics for this release:
    - path patterns use normalized `/` separators;
    - `foo/**` matches `foo` and anything below it;
    - `**/*.pyc`-style suffix patterns also match root-level files;
    - a bare segment/basename pattern (for example `node_modules` or `.nfs*`)
      matches any path segment.
    """
    rel = _normalize_rel(relative_path)
    if not rel or rel == ".":
        return False
    parts = rel.split("/")
    for raw_pattern in patterns:
        pattern = _normalize_pattern(raw_pattern)
        if not pattern:
            continue
        if "/" not in pattern:
            if any(fnmatch.fnmatchcase(part, pattern) for part in parts):
                return True
            continue
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/")
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
        if fnmatch.fnmatchcase(rel, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatchcase(rel, pattern[3:]):
            return True
    return False


def parse_roots(value: str | None, cwd: str | os.PathLike[str]) -> tuple[list[Path], list[SnapshotDiagnostic]]:
    """Resolve, skip missing, and de-duplicate watched roots."""
    raw_roots = [p for p in (value or "").split(os.pathsep) if p.strip()]
    if not raw_roots:
        raw_roots = [str(cwd)]

    diagnostics: list[SnapshotDiagnostic] = []
    candidates: list[Path] = []
    seen: set[str] = set()
    for raw in raw_roots:
        root = Path(raw).expanduser()
        if not root.is_absolute():
            root = Path(cwd, root)
        try:
            resolved = root.resolve(strict=True)
        except FileNotFoundError:
            diagnostics.append(
                SnapshotDiagnostic("missing_root", f"snapshot root does not exist: {root}", root=str(root))
            )
            continue
        except OSError as exc:
            diagnostics.append(
                SnapshotDiagnostic("root_error", f"cannot resolve snapshot root {root}: {exc}", root=str(root))
            )
            continue
        if not resolved.is_dir():
            diagnostics.append(
                SnapshotDiagnostic("not_directory", f"snapshot root is not a directory: {resolved}", root=str(resolved))
            )
            continue
        key = str(resolved)
        if key in seen:
            diagnostics.append(
                SnapshotDiagnostic("duplicate_root", f"duplicate snapshot root skipped: {resolved}", root=key)
            )
            continue
        seen.add(key)
        candidates.append(resolved)

    candidates.sort(key=lambda p: (len(p.parts), str(p)))
    kept: list[Path] = []
    for root in candidates:
        ancestor = next((existing for existing in kept if _is_relative_to(root, existing)), None)
        if ancestor is not None:
            diagnostics.append(
                SnapshotDiagnostic(
                    "overlapping_root",
                    f"snapshot root {root} is inside {ancestor}; keeping ancestor only",
                    root=str(root),
                    path=str(ancestor),
                )
            )
            continue
        kept.append(root)

    if not kept:
        diagnostics.append(SnapshotDiagnostic("no_roots", "no usable snapshot roots remain"))
    return kept, diagnostics


def capture_manifest(
    roots: Iterable[str | os.PathLike[str]],
    *,
    hash_files: bool = False,
    exclude_patterns: Iterable[str] = BUILTIN_EXCLUDES,
    max_files: int | None = None,
) -> Manifest:
    """Synchronously inventory configured roots without following symlink subtrees."""
    manifest = Manifest()
    patterns = [_normalize_pattern(p) for p in exclude_patterns]
    for rootish in roots:
        root = Path(rootish)
        try:
            root = root.resolve(strict=True)
        except OSError as exc:
            manifest.complete = False
            manifest.diagnostics.append(
                SnapshotDiagnostic("root_error", f"cannot resolve snapshot root {rootish}: {exc}", root=str(rootish))
            )
            continue
        _walk_root(root, root, manifest, hash_files=hash_files, patterns=patterns, max_files=max_files)
    return manifest


def diff_manifests(before: dict[str, ManifestEntry] | Manifest, after: dict[str, ManifestEntry] | Manifest) -> list[dict[str, Any]]:
    """Return deterministic manifest diff records."""
    before_entries = before.entries if isinstance(before, Manifest) else before
    after_entries = after.entries if isinstance(after, Manifest) else after
    before_paths = set(before_entries)
    after_paths = set(after_entries)

    deleted = before_paths - after_paths
    created = after_paths - before_paths
    records: list[dict[str, Any]] = []

    rename_pairs = _detect_renames(deleted, created, before_entries, after_entries)
    renamed_deleted = {old for old, _new in rename_pairs}
    renamed_created = {new for _old, new in rename_pairs}

    for old_path, new_path in rename_pairs:
        records.append({"operation": "rename", "old_path": old_path, "new_path": new_path, "before": before_entries[old_path], "after": after_entries[new_path]})

    for path in sorted(deleted - renamed_deleted):
        records.append({"operation": "delete", "path": path, "before": before_entries[path], "after": None})
    for path in sorted(created - renamed_created):
        records.append({"operation": "create", "path": path, "before": None, "after": after_entries[path]})

    for path in sorted(before_paths & after_paths):
        old = before_entries[path]
        new = after_entries[path]
        op = _changed_operation(old, new)
        if op is None:
            continue
        records.append({"operation": op, "path": path, "before": old, "after": new})

    records.sort(key=lambda r: (r.get("path") or r.get("new_path") or "", r["operation"], r.get("old_path") or ""))
    return records


def synthesize_events(
    diff_records: Iterable[dict[str, Any]],
    *,
    session_id: str,
    invocation_id: str | None = None,
    timestamp: str | None = None,
) -> list[dict[str, Any]]:
    """Convert manifest diff records to schema-v2 snapshot/inferred JSON events."""
    ts = timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    invocation = invocation_id or session_id
    events: list[dict[str, Any]] = []
    for record in diff_records:
        before = record.get("before")
        after = record.get("after")
        path = record.get("path") or record.get("new_path")
        event = {
            "schema_version": SCHEMA_VERSION,
            "timestamp": ts,
            "session_id": session_id,
            "invocation_id": invocation,
            "operation": record["operation"],
            "path": path,
            "old_path": record.get("old_path"),
            "new_path": record.get("new_path"),
            "source": "snapshot",
            "confidence": "inferred",
            "snapshot": {
                "before": before.to_public_dict() if isinstance(before, ManifestEntry) else None,
                "after": after.to_public_dict() if isinstance(after, ManifestEntry) else None,
            },
            "result": 0,
        }
        identity_source = after if isinstance(after, ManifestEntry) else before
        if isinstance(identity_source, ManifestEntry) and identity_source.object_identity:
            dev, ino = identity_source.object_identity
            event["object"] = {"dev": dev, "ino": ino}
        events.append(event)
    return events


def deduplicate_snapshot_events(
    snapshot_events: Iterable[dict[str, Any]],
    direct_events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Suppress snapshot events only when direct strace evidence covers them."""
    direct_create: set[str] = set()
    direct_modify_or_create: set[str] = set()
    direct_delete: set[str] = set()
    direct_metadata: set[str] = set()
    direct_renames: set[tuple[str, str]] = set()

    for event in direct_events:
        source = event.get("source") or "strace"
        if source != "strace":
            continue
        op = event.get("operation")
        if op == "rename":
            old_path = _normalize_event_path(event.get("old_path"))
            new_path = _normalize_event_path(event.get("new_path"))
            if old_path and new_path:
                direct_renames.add((old_path, new_path))
            continue
        path = _normalize_event_path(event.get("path"))
        if path is None:
            continue
        if op == "create":
            direct_create.add(path)
            direct_modify_or_create.add(path)
        elif op == "modify":
            direct_modify_or_create.add(path)
        elif op == "delete":
            direct_delete.add(path)
        elif op == "metadata":
            direct_metadata.add(path)

    filtered: list[dict[str, Any]] = []
    for event in snapshot_events:
        op = event.get("operation")
        if op == "rename":
            old_path = _normalize_event_path(event.get("old_path"))
            new_path = _normalize_event_path(event.get("new_path"))
            if old_path and new_path and (old_path, new_path) in direct_renames:
                continue
            filtered.append(event)
            continue

        path = _normalize_event_path(event.get("path"))
        if path is None:
            filtered.append(event)
            continue
        if op == "create" and path in direct_create:
            continue
        if op == "delete" and path in direct_delete:
            continue
        if op == "metadata" and path in direct_metadata:
            continue
        if op == "modify" and path in direct_modify_or_create:
            continue
        filtered.append(event)
    return filtered


def _walk_root(
    root: Path,
    current: Path,
    manifest: Manifest,
    *,
    hash_files: bool,
    patterns: list[str],
    max_files: int | None,
) -> None:
    if max_files is not None and len(manifest.entries) >= max_files:
        _cap_exceeded(manifest, str(root))
        return
    try:
        with os.scandir(current) as it:
            entries = sorted(list(it), key=lambda e: e.name)
    except OSError as exc:
        manifest.complete = False
        manifest.diagnostics.append(
            SnapshotDiagnostic("unreadable_path", f"cannot read snapshot path {current}: {exc}", root=str(root), path=str(current))
        )
        return

    for entry in entries:
        path = Path(entry.path)
        rel = _normalize_rel(path.relative_to(root))
        if should_exclude(rel, patterns):
            continue
        if max_files is not None and len(manifest.entries) >= max_files:
            _cap_exceeded(manifest, str(root))
            return
        try:
            st = entry.stat(follow_symlinks=False)
            manifest.entries[_absolute_path(path)] = _entry_from_stat(path, st, hash_files=hash_files, manifest=manifest, root=root)
        except OSError as exc:
            manifest.complete = False
            manifest.diagnostics.append(
                SnapshotDiagnostic("unreadable_path", f"cannot stat snapshot path {path}: {exc}", root=str(root), path=str(path))
            )
            continue
        if entry.is_dir(follow_symlinks=False):
            _walk_root(root, path, manifest, hash_files=hash_files, patterns=patterns, max_files=max_files)
            if max_files is not None and len(manifest.entries) >= max_files and not manifest.complete:
                return


def _entry_from_stat(path: Path, st: os.stat_result, *, hash_files: bool, manifest: Manifest, root: Path) -> ManifestEntry:
    mode = st.st_mode
    entry_type = _file_type(mode)
    size = st.st_size if entry_type == "file" else None
    target = os.readlink(path) if entry_type == "symlink" else None
    digest: str | None = None
    if hash_files and entry_type == "file":
        try:
            digest = _hash_file(path)
        except OSError as exc:
            manifest.complete = False
            manifest.diagnostics.append(
                SnapshotDiagnostic("hash_error", f"cannot hash snapshot file {path}: {exc}", root=str(root), path=str(path))
            )
            digest = None
    return ManifestEntry(
        path=_absolute_path(path),
        type=entry_type,
        size=size,
        mtime_ns=st.st_mtime_ns,
        ctime_ns=st.st_ctime_ns,
        mode=mode,
        root=str(root),
        dev=st.st_dev,
        ino=st.st_ino,
        symlink_target=target,
        hash=digest,
    )


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _file_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"


def _detect_renames(
    deleted: set[str],
    created: set[str],
    before: dict[str, ManifestEntry],
    after: dict[str, ManifestEntry],
) -> list[tuple[str, str]]:
    deleted_by_id: dict[tuple[str, int, int], list[str]] = {}
    created_by_id: dict[tuple[str, int, int], list[str]] = {}
    for path in deleted:
        entry = before[path]
        ident = entry.object_identity
        if ident is not None and entry.root is not None:
            deleted_by_id.setdefault((entry.root, ident[0], ident[1]), []).append(path)
    for path in created:
        entry = after[path]
        ident = entry.object_identity
        if ident is not None and entry.root is not None:
            created_by_id.setdefault((entry.root, ident[0], ident[1]), []).append(path)
    pairs: list[tuple[str, str]] = []
    for ident in sorted(set(deleted_by_id) & set(created_by_id)):
        old_paths = sorted(deleted_by_id[ident])
        new_paths = sorted(created_by_id[ident])
        if len(old_paths) == 1 and len(new_paths) == 1:
            old = old_paths[0]
            new = new_paths[0]
            if before[old].type == after[new].type:
                pairs.append((old, new))
    return pairs


def _changed_operation(old: ManifestEntry, new: ManifestEntry) -> str | None:
    hash_changed = old.hash is not None and new.hash is not None and old.hash != new.hash
    hashes_compatible = old.hash == new.hash or old.hash is None or new.hash is None
    if (
        old.type == new.type
        and old.size == new.size
        and old.mtime_ns == new.mtime_ns
        and hashes_compatible
        and old.mode == new.mode
        and old.symlink_target == new.symlink_target
    ):
        # ctime alone is intentionally ignored.
        return None
    if old.type == "file" and new.type == "file" and (
        old.size != new.size or old.mtime_ns != new.mtime_ns or hash_changed
    ):
        return "modify"
    if old.type != new.type or old.mode != new.mode or old.symlink_target != new.symlink_target:
        return "metadata"
    return None


def _cap_exceeded(manifest: Manifest, root: str) -> None:
    if any(d.code == "max_files_exceeded" and d.root == root for d in manifest.diagnostics):
        return
    manifest.complete = False
    manifest.diagnostics.append(
        SnapshotDiagnostic("max_files_exceeded", f"snapshot max-files cap exceeded under {root}", root=root)
    )


def _normalize_rel(path: str | os.PathLike[str]) -> str:
    return Path(path).as_posix().strip("/")


def _normalize_pattern(pattern: str) -> str:
    return pattern.replace(os.sep, "/").strip().strip("/")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _absolute_path(path: Path) -> str:
    return os.path.abspath(os.fspath(path))


def _normalize_event_path(path: Any) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    return os.path.abspath(os.path.normpath(path))
