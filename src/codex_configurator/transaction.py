from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .endpoints import PROVIDER_ID
from .errors import BackupSpaceError, TransactionError
from .progress import ProgressCallback, emit_progress
from .sessions import (
    RolloutChange,
    backup_sqlite,
    ensure_sqlite_ready,
    sqlite_path,
    update_sqlite_provider,
)


@dataclass(frozen=True)
class SetupChanges:
    config_path: Path
    config_content: bytes
    catalog_path: Path
    catalog_content: bytes
    rollout_changes: tuple[RolloutChange, ...] = ()
    migrate_sessions: bool = False


@dataclass(frozen=True)
class BackupSpaceEstimate:
    backup_bytes: int
    local_temp_bytes: int


BACKUP_FORMAT_VERSION = 2
SPACE_MARGIN_BYTES = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_to_home(path: Path, codex_home: Path) -> Path:
    try:
        return path.resolve().relative_to(codex_home.resolve())
    except ValueError as exc:
        raise TransactionError(f"拒绝修改 CODEX_HOME 之外的路径：{path}") from exc


def atomic_write(path: Path, content: bytes, *, mtime_ns: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        if mtime_ns is not None:
            os.utime(path, ns=(mtime_ns, mtime_ns))
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_rewrite_rollout(change: RolloutChange) -> None:
    path = change.path
    before = path.stat()
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with path.open("rb") as source, os.fdopen(descriptor, "wb") as target:
            opened_before = os.fstat(source.fileno())
            current_first_line = source.readline()
            original_first_line = change.original_first_line or current_first_line
            if (
                before.st_mtime_ns != change.mtime_ns
                or current_first_line != original_first_line
            ):
                raise TransactionError(f"会话文件在写入前已发生变化：{path}")
            target.write(change.updated_first_line)
            shutil.copyfileobj(source, target, length=1024 * 1024)
            opened_after = os.fstat(source.fileno())
            target.flush()
            os.fsync(target.fileno())
        after = path.stat()
        if len(
            {
                _stat_signature(before),
                _stat_signature(opened_before),
                _stat_signature(opened_after),
                _stat_signature(after),
            }
        ) != 1:
            raise TransactionError(f"会话文件在写入期间发生变化：{path}")
        os.replace(temp_path, path)
        os.utime(path, ns=(change.mtime_ns, change.mtime_ns))
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_restore_rollout(
    path: Path, original_first_line: bytes, *, mtime_ns: int
) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with path.open("rb") as source, os.fdopen(descriptor, "wb") as target:
            source.readline()
            target.write(original_first_line)
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_path, path)
        os.utime(path, ns=(mtime_ns, mtime_ns))
    finally:
        temp_path.unlink(missing_ok=True)


def _tail_info(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        handle.readline()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _stat_signature(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def _snapshot_rollout(path: Path) -> tuple[bytes, int, int, str, os.stat_result]:
    before = path.stat()
    digest = hashlib.sha256()
    tail_size = 0
    with path.open("rb") as handle:
        opened_before = os.fstat(handle.fileno())
        first_line = handle.readline()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            tail_size += len(chunk)
            digest.update(chunk)
        opened_after = os.fstat(handle.fileno())
    after = path.stat()
    signatures = {
        _stat_signature(before),
        _stat_signature(opened_before),
        _stat_signature(opened_after),
        _stat_signature(after),
    }
    if len(signatures) != 1 or len(first_line) + tail_size != after.st_size:
        raise TransactionError(f"会话文件在备份期间发生变化：{path}")
    return first_line, after.st_size, tail_size, digest.hexdigest(), after


def _backup_root(codex_home: Path, backup_root: Path | None) -> Path:
    home = codex_home.resolve()
    default = (home / "backup-xi-ai").resolve()
    if backup_root is None:
        return default
    resolved = backup_root.expanduser().resolve()
    if resolved == default:
        return resolved
    try:
        resolved.relative_to(home)
    except ValueError:
        return resolved
    raise TransactionError(
        "备用备份目录不能位于 CODEX_HOME 内；请使用其他磁盘或目录"
    )


def _nearest_existing_directory(path: Path) -> Path:
    current = path.expanduser()
    while not current.exists() and current != current.parent:
        current = current.parent
    if not current.is_dir():
        raise TransactionError(f"备份目录的父路径不可用：{path}")
    return current


def _same_filesystem(left: Path, right: Path) -> bool:
    left_existing = _nearest_existing_directory(left)
    right_existing = _nearest_existing_directory(right)
    try:
        return left_existing.stat().st_dev == right_existing.stat().st_dev
    except OSError:
        if os.name == "nt":
            return os.path.splitdrive(str(left_existing))[0].lower() == os.path.splitdrive(
                str(right_existing)
            )[0].lower()
        raise


def _format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def estimate_backup_space(
    codex_home: Path, changes: SetupChanges
) -> BackupSpaceEstimate:
    full_bytes = 0
    for path in (changes.config_path, changes.catalog_path):
        if path.is_file():
            full_bytes += path.stat().st_size

    patch_bytes = 0
    largest_rollout = 0
    for change in changes.rollout_changes:
        patch_bytes += len(change.original_first_line) + len(change.path.as_posix()) + 512
        if change.path.is_file():
            current_size = change.path.stat().st_size
            updated_size = (
                current_size
                - len(change.original_first_line)
                + len(change.updated_first_line)
            )
            largest_rollout = max(largest_rollout, current_size, updated_size)

    database = sqlite_path(codex_home)
    sqlite_bytes = 0
    if changes.migrate_sessions and database.is_file():
        sqlite_bytes = database.stat().st_size
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{database}{suffix}")
            if sidecar.is_file():
                sqlite_bytes += sidecar.stat().st_size
    backup_payload = full_bytes + patch_bytes + (sqlite_bytes * 2)
    backup_bytes = backup_payload + max(SPACE_MARGIN_BYTES, backup_payload // 20)
    local_temp_bytes = max(
        largest_rollout,
        changes.config_path.stat().st_size if changes.config_path.is_file() else 0,
        len(changes.config_content),
        changes.catalog_path.stat().st_size if changes.catalog_path.is_file() else 0,
        len(changes.catalog_content),
        sqlite_bytes * 2,
    ) + SPACE_MARGIN_BYTES
    return BackupSpaceEstimate(backup_bytes, local_temp_bytes)


def _check_backup_space(
    codex_home: Path,
    backup_root: Path,
    changes: SetupChanges,
) -> BackupSpaceEstimate:
    estimate = estimate_backup_space(codex_home, changes)
    try:
        destination_parent = _nearest_existing_directory(backup_root)
        destination_free = shutil.disk_usage(destination_parent).free
        home_free = shutil.disk_usage(_nearest_existing_directory(codex_home)).free
        same_filesystem = _same_filesystem(backup_root, codex_home)
    except OSError as exc:
        raise TransactionError(
            "无法检查备份目录的磁盘空间；请确认路径可访问"
        ) from exc
    required_destination = estimate.backup_bytes
    if same_filesystem:
        required_destination += estimate.local_temp_bytes
    if destination_free < required_destination:
        raise BackupSpaceError(
            "备份空间不足：需要约 "
            f"{_format_bytes(required_destination)}，可用 "
            f"{_format_bytes(destination_free)}（目录：{backup_root}）"
        )
    if not same_filesystem and home_free < estimate.local_temp_bytes:
        raise BackupSpaceError(
            "CODEX_HOME 所在磁盘空间不足：原子写入至少需要约 "
            f"{_format_bytes(estimate.local_temp_bytes)}，可用 "
            f"{_format_bytes(home_free)}"
        )
    return estimate


def check_backup_space(
    codex_home: Path,
    changes: SetupChanges,
    backup_root: Path | None = None,
) -> BackupSpaceEstimate:
    """Validate backup and transaction space without creating files."""

    return _check_backup_space(codex_home, _backup_root(codex_home, backup_root), changes)


def candidate_backup_roots(
    codex_home: Path, changes: SetupChanges
) -> tuple[Path, ...]:
    """Return mounted alternate roots that pass the same safety preflight."""

    candidates: list[Path] = []
    if os.name == "nt":
        current_drive = os.path.splitdrive(str(codex_home.resolve()))[0].lower()
        for code in range(ord("A"), ord("Z") + 1):
            root = Path(f"{chr(code)}:\\Xi-AI-Backups")
            if not Path(root.anchor or root).exists():
                continue
            if os.path.splitdrive(str(root))[0].lower() == current_drive:
                continue
            try:
                _check_backup_space(codex_home, root, changes)
            except (BackupSpaceError, TransactionError, OSError):
                continue
            candidates.append(root)
    else:
        roots = [Path("/mnt"), Path("/media"), Path("/Volumes")]
        for parent in roots:
            if not parent.is_dir():
                continue
            for mount in parent.iterdir():
                root = mount / "Xi-AI-Backups"
                try:
                    _check_backup_space(codex_home, root, changes)
                except (BackupSpaceError, TransactionError, OSError):
                    continue
                candidates.append(root)
    return tuple(candidates)


def _backup_file(path: Path, codex_home: Path, backup_dir: Path) -> dict:
    relative = _relative_to_home(path, codex_home)
    entry = {"path": relative.as_posix(), "existed": path.is_file(), "kind": "full"}
    if path.is_file():
        target = backup_dir / "files" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        before = path.stat()
        digest = hashlib.sha256()
        copied_size = 0
        with path.open("rb") as source, target.open("xb") as destination:
            opened_before = os.fstat(source.fileno())
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                copied_size += len(chunk)
                digest.update(chunk)
                destination.write(chunk)
            opened_after = os.fstat(source.fileno())
            destination.flush()
            os.fsync(destination.fileno())
        after = path.stat()
        signatures = {
            _stat_signature(before),
            _stat_signature(opened_before),
            _stat_signature(opened_after),
            _stat_signature(after),
        }
        if (
            len(signatures) != 1
            or copied_size != after.st_size
            or target.stat().st_size != copied_size
        ):
            raise TransactionError(
                f"文件在备份期间发生变化：{relative.as_posix()}"
            )
        entry.update(
            {
                "backup": target.relative_to(backup_dir).as_posix(),
                "sha256": digest.hexdigest(),
                "mtime_ns": after.st_mtime_ns,
            }
        )
    return entry


def _backup_rollout(
    change: RolloutChange,
    codex_home: Path,
    backup_dir: Path,
    ordinal: int,
) -> dict:
    relative = _relative_to_home(change.path, codex_home)
    if not change.path.is_file():
        raise TransactionError(f"会话文件在备份前已消失：{relative.as_posix()}")
    current_first_line, file_size, tail_size, tail_hash, snapshot_stat = (
        _snapshot_rollout(change.path)
    )
    original_first_line = change.original_first_line or current_first_line
    if (
        snapshot_stat.st_mtime_ns != change.mtime_ns
        or current_first_line != original_first_line
    ):
        raise TransactionError(f"会话文件在备份前已发生变化：{relative.as_posix()}")
    patch = backup_dir / "rollouts" / f"{ordinal:06d}.patch"
    atomic_write(patch, original_first_line)
    final_before = change.path.stat()
    with change.path.open("rb") as source:
        final_opened = os.fstat(source.fileno())
        final_first_line = source.readline()
    final_after = change.path.stat()
    if any(
        _stat_signature(stat_result) != _stat_signature(snapshot_stat)
        for stat_result in (final_before, final_opened, final_after)
    ) or final_first_line != current_first_line:
        raise TransactionError(f"会话文件在备份期间发生变化：{relative.as_posix()}")
    return {
        "path": relative.as_posix(),
        "existed": True,
        "kind": "rollout_first_line",
        "backup": patch.relative_to(backup_dir).as_posix(),
        "sha256": sha256_file(patch),
        "size": len(original_first_line),
        "file_size": file_size,
        "tail_size": tail_size,
        "tail_sha256": tail_hash,
        "mtime_ns": change.mtime_ns,
    }


def create_backup(
    codex_home: Path,
    changes: SetupChanges,
    *,
    backup_root: Path | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    root = _backup_root(codex_home, backup_root)
    _check_backup_space(codex_home, root, changes)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = root / stamp
    try:
        backup_dir.mkdir(parents=True, exist_ok=False)
        _check_backup_space(codex_home, root, changes)
        targets: list[Path] = [changes.config_path, changes.catalog_path]
        emit_progress(
            progress,
            "backup_files",
            "备份配置和会话文件",
            "start",
            current=0,
            total=len(targets) + len(changes.rollout_changes),
        )
        file_entries = []
        for index, path in enumerate(targets, start=1):
            file_entries.append(_backup_file(path, codex_home, backup_dir))
            emit_progress(
                progress,
                "backup_files",
                "备份配置和会话文件",
                "update",
                current=index,
                total=len(targets) + len(changes.rollout_changes),
            )
        for index, change in enumerate(changes.rollout_changes, start=1):
            file_entries.append(
                _backup_rollout(change, codex_home, backup_dir, index)
            )
            emit_progress(
                progress,
                "backup_files",
                "备份配置和会话文件",
                "update",
                current=len(targets) + index,
                total=len(targets) + len(changes.rollout_changes),
            )
        emit_progress(
            progress,
            "backup_files",
            "备份配置和会话文件",
            "complete",
            current=len(targets) + len(changes.rollout_changes),
            total=len(targets) + len(changes.rollout_changes),
        )

        database = sqlite_path(codex_home)
        sqlite_entry = {"path": "state_5.sqlite", "existed": False}
        if changes.migrate_sessions and database.is_file():
            snapshot = backup_dir / "db" / "state_5.sqlite"
            emit_progress(
                progress, "backup_sqlite", "备份会话数据库", "start"
            )
            backup_sqlite(database, snapshot)
            emit_progress(
                progress, "backup_sqlite", "备份会话数据库", "complete"
            )
            sqlite_entry = {
                "path": "state_5.sqlite",
                "existed": True,
                "backup": snapshot.relative_to(backup_dir).as_posix(),
                "sha256": sha256_file(snapshot),
            }

        manifest = {
            "version": BACKUP_FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "provider": PROVIDER_ID,
            "codex_home": str(codex_home.resolve()),
            "files": file_entries,
            "sqlite": sqlite_entry,
            "session_migration": changes.migrate_sessions,
        }
        atomic_write(
            backup_dir / "manifest.json",
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        return backup_dir
    except OSError as exc:
        shutil.rmtree(backup_dir, ignore_errors=True)
        if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
            raise BackupSpaceError(
                "备份过程中磁盘空间不足；请指定其他备份目录后重试"
            ) from exc
        if isinstance(exc, PermissionError):
            raise TransactionError("备份目录不可写；请指定其他备份目录后重试") from exc
        raise TransactionError("无法创建备份；请检查备份路径和磁盘状态") from exc
    except Exception:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise


def _load_manifest(
    codex_home: Path,
    backup_dir: Path,
    *,
    backup_root: Path | None = None,
) -> dict:
    root = _backup_root(codex_home, backup_root)
    resolved = backup_dir.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise TransactionError("备份路径位于 Xi-AI 备份根目录之外") from exc
    if resolved == root or resolved.parent != root:
        raise TransactionError("该路径不是有效的 Xi-AI 备份目录")
    manifest_path = resolved / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransactionError("备份清单缺失或无效") from exc
    if not isinstance(manifest, dict):
        raise TransactionError("备份清单缺失或无效")
    if type(manifest.get("version")) is not int or manifest.get("version") not in {
        1,
        BACKUP_FORMAT_VERSION,
    } or not isinstance(
        manifest.get("session_migration"), bool
    ):
        raise TransactionError("备份清单格式不受支持")
    if manifest.get("provider") != PROVIDER_ID:
        raise TransactionError("备份清单不属于 Xi-AI")
    manifest_home = manifest.get("codex_home")
    if not isinstance(manifest_home, str):
        raise TransactionError("备份清单中的 CODEX_HOME 无效")
    if Path(manifest_home).resolve() != codex_home.resolve():
        raise TransactionError("备份清单属于另一个 CODEX_HOME")
    if not isinstance(manifest.get("files"), list):
        raise TransactionError("备份清单中的文件条目无效")
    sqlite_entry = manifest.get("sqlite")
    if not isinstance(sqlite_entry, dict) or not isinstance(
        sqlite_entry.get("existed"), bool
    ):
        raise TransactionError("备份清单中的 SQLite 元数据无效")
    if sqlite_entry.get("path") != "state_5.sqlite":
        raise TransactionError("备份清单中的 SQLite 路径无效")
    return manifest


def _resolved_backup_source(backup_dir: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise TransactionError("备份清单包含无效的源路径")
    source = (backup_dir / relative).resolve()
    try:
        source.relative_to(backup_dir.resolve())
    except ValueError as exc:
        raise TransactionError("备份清单包含根目录之外的路径") from exc
    return source


def _manifest_target(codex_home: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise TransactionError("备份清单包含无效的目标路径")
    target = codex_home / relative
    _relative_to_home(target, codex_home)
    try:
        target.resolve().relative_to((codex_home / "backup-xi-ai").resolve())
    except ValueError:
        return target
    raise TransactionError("备份清单将自身备份目录设为了目标")


def _is_rollout_path(relative: str) -> bool:
    path = Path(relative)
    return (
        path.suffix.lower() == ".jsonl"
        and len(path.parts) >= 2
        and path.parts[0] in {"sessions", "archived_sessions"}
    )


def _validate_hash(value: object, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise TransactionError(f"备份清单中的 {label} 哈希无效")
    try:
        int(value, 16)
    except ValueError as exc:
        raise TransactionError(f"备份清单中的 {label} 哈希无效") from exc
    return value


def restore_backup(
    codex_home: Path,
    backup_dir: Path,
    *,
    backup_root: Path | None = None,
) -> None:
    manifest = _load_manifest(codex_home, backup_dir, backup_root=backup_root)
    restore_entries: list[
        tuple[dict, Path, Path | None, str | None, int | None, str]
    ] = []
    seen_targets: set[Path] = set()
    manifest_version = manifest["version"]
    sqlite_entry = manifest.get("sqlite", {})
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("existed"), bool):
            raise TransactionError("备份清单中的文件元数据无效")
        relative_path = entry.get("path")
        target = _manifest_target(codex_home, relative_path)
        if relative_path == "state_5.sqlite":
            raise TransactionError("备份清单不得将 SQLite 文件放入普通文件条目")
        resolved_target = target.resolve()
        if resolved_target == sqlite_path(codex_home).resolve():
            raise TransactionError("备份清单不得重复声明 SQLite 目标")
        if resolved_target in seen_targets:
            raise TransactionError("备份清单包含重复文件路径")
        seen_targets.add(resolved_target)
        # v1 manifests are always full-file backups. Do not let a modified or
        # malformed legacy manifest reinterpret a full file as a compact patch.
        if manifest_version == 1:
            kind = "full"
        else:
            kind = entry.get("kind")
        if kind not in {"full", "rollout_first_line"}:
            raise TransactionError("备份清单中的备份类型无效")
        if kind == "rollout_first_line":
            if not entry["existed"] or not isinstance(relative_path, str) or not _is_rollout_path(relative_path):
                raise TransactionError("紧凑备份条目必须是已存在的会话 JSONL 文件")
        if entry["existed"]:
            source = _resolved_backup_source(backup_dir, entry.get("backup"))
            expected_hash = _validate_hash(entry.get("sha256"), label="file")
            if not source.is_file() or sha256_file(source) != expected_hash:
                raise TransactionError(f"备份文件缺失或损坏：{entry['path']}")
            mtime_ns = entry.get("mtime_ns")
            if not isinstance(mtime_ns, int):
                raise TransactionError("备份清单中的文件时间戳无效")
            if kind == "rollout_first_line":
                size = entry.get("size")
                file_size = entry.get("file_size")
                tail_hash = _validate_hash(entry.get("tail_sha256"), label="会话尾部")
                if type(size) is not int or size != source.stat().st_size:
                    raise TransactionError("会话首行补丁大小不匹配")
                tail_size = entry.get("tail_size")
                if (
                    type(file_size) is not int
                    or file_size < size
                    or type(tail_size) is not int
                    or tail_size != file_size - size
                ):
                    raise TransactionError("会话文件大小元数据无效")
                if not target.is_file():
                    raise TransactionError(f"会话文件在恢复前已发生变化：{entry['path']}")
                current_tail_size, current_tail_hash = _tail_info(target)
                if current_tail_size != tail_size or current_tail_hash != tail_hash:
                    raise TransactionError(f"会话内容校验失败：{entry['path']}")
            restore_entries.append(
                (entry, target, source, expected_hash, mtime_ns, kind)
            )
        else:
            restore_entries.append((entry, target, None, None, None, kind))

    sqlite_source: Path | None = None
    sqlite_hash: str | None = None
    if sqlite_entry.get("existed"):
        sqlite_source = _resolved_backup_source(backup_dir, sqlite_entry.get("backup"))
        sqlite_hash = _validate_hash(sqlite_entry.get("sha256"), label="SQLite")
        if not sqlite_source.is_file() or sha256_file(sqlite_source) != sqlite_hash:
            raise TransactionError("SQLite 备份缺失或损坏")
    if sqlite_entry.get("existed") or manifest.get("session_migration"):
        ensure_sqlite_ready(sqlite_path(codex_home))

    for entry, target, source, _, mtime_ns, kind in restore_entries:
        if entry["existed"]:
            assert source is not None
            assert mtime_ns is not None
            if kind == "rollout_first_line":
                atomic_restore_rollout(target, source.read_bytes(), mtime_ns=mtime_ns)
            else:
                atomic_write(target, source.read_bytes(), mtime_ns=mtime_ns)
        else:
            target.unlink(missing_ok=True)

    database = sqlite_path(codex_home)
    if sqlite_source is not None:
        atomic_write(database, sqlite_source.read_bytes())
    elif manifest.get("session_migration"):
        database.unlink(missing_ok=True)

    for entry, target, source, expected_hash, _, kind in restore_entries:
        if entry["existed"]:
            assert source is not None
            if kind == "rollout_first_line":
                if (
                    not target.is_file()
                ):
                    raise TransactionError(f"恢复后的会话大小不匹配：{entry['path']}")
                current_tail_size, current_tail_hash = _tail_info(target)
                if (
                    current_tail_size != entry["tail_size"]
                    or current_tail_hash != entry["tail_sha256"]
                ):
                    raise TransactionError(f"恢复后的会话内容不匹配：{entry['path']}")
                with target.open("rb") as handle:
                    if handle.readline() != source.read_bytes():
                        raise TransactionError(f"恢复后的会话首行不匹配：{entry['path']}")
            else:
                assert expected_hash is not None
                if not target.is_file() or sha256_file(target) != expected_hash:
                    raise TransactionError(f"恢复后的文件哈希不匹配：{entry['path']}")
        elif target.exists():
            raise TransactionError(f"恢复时未删除新增文件：{entry['path']}")
    if sqlite_source is not None and (
        not database.is_file() or sha256_file(database) != sqlite_hash
    ):
        raise TransactionError("恢复后的 SQLite 哈希不匹配")


def latest_backup(codex_home: Path, backup_root: Path | None = None) -> Path:
    root = _backup_root(codex_home, backup_root)
    backups = sorted(path for path in root.glob("*") if (path / "manifest.json").is_file())
    if not backups:
        raise TransactionError("没有可用的 Xi-AI 备份")
    return backups[-1]


def apply_setup(
    codex_home: Path,
    changes: SetupChanges,
    *,
    fail_at: str | None = None,
    allow_wal_recovery: bool = False,
    backup_root: Path | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    codex_home.mkdir(parents=True, exist_ok=True)
    emit_progress(progress, "setup", "应用配置", "start")
    if changes.migrate_sessions:
        emit_progress(
            progress, "sqlite_ready", "检查会话数据库", "start"
        )
        ensure_sqlite_ready(
            sqlite_path(codex_home),
            allow_wal_recovery=allow_wal_recovery,
        )
        emit_progress(
            progress, "sqlite_ready", "检查会话数据库", "complete"
        )
    backup_dir = create_backup(
        codex_home,
        changes,
        backup_root=backup_root,
        progress=progress,
    )
    try:
        emit_progress(progress, "write_config", "写入 Codex 配置", "start")
        atomic_write(changes.config_path, changes.config_content)
        emit_progress(progress, "write_config", "写入 Codex 配置", "complete")
        if fail_at == "config":
            raise RuntimeError("injected failure after config")
        emit_progress(progress, "write_catalog", "写入模型目录", "start")
        atomic_write(changes.catalog_path, changes.catalog_content)
        emit_progress(progress, "write_catalog", "写入模型目录", "complete")
        if fail_at == "catalog":
            raise RuntimeError("injected failure after catalog")
        rollout_total = len(changes.rollout_changes)
        emit_progress(
            progress,
            "rewrite_rollouts",
            "更新会话索引",
            "start",
            current=0,
            total=rollout_total,
        )
        for index, change in enumerate(changes.rollout_changes, start=1):
            atomic_rewrite_rollout(change)
            emit_progress(
                progress,
                "rewrite_rollouts",
                "更新会话索引",
                "update",
                current=index,
                total=rollout_total,
            )
        emit_progress(
            progress,
            "rewrite_rollouts",
            "更新会话索引",
            "complete",
            current=rollout_total,
            total=rollout_total,
        )
        if fail_at == "rollouts":
            raise RuntimeError("injected failure after rollouts")
        if changes.migrate_sessions:
            emit_progress(
                progress, "update_sqlite", "更新会话数据库", "start"
            )
            update_sqlite_provider(sqlite_path(codex_home), PROVIDER_ID)
            emit_progress(
                progress, "update_sqlite", "更新会话数据库", "complete"
            )
        if fail_at == "sqlite":
            raise RuntimeError("injected failure after sqlite")
    except Exception as exc:
        emit_progress(progress, "rollback", "自动恢复原配置", "start")
        try:
            restore_backup(codex_home, backup_dir, backup_root=backup_root)
            emit_progress(progress, "rollback", "自动恢复原配置", "complete")
        except Exception as restore_exc:
            raise TransactionError(
                f"配置失败，自动恢复也失败：{restore_exc}"
            ) from exc
        raise TransactionError(f"配置失败，已自动回滚：{exc}") from exc
    emit_progress(progress, "setup", "应用配置", "complete")
    return backup_dir
