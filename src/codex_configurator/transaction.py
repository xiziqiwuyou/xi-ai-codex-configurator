from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .endpoints import PROVIDER_ID
from .errors import TransactionError
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
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with path.open("rb") as source, os.fdopen(descriptor, "wb") as target:
            source.readline()
            target.write(change.updated_first_line)
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_path, path)
        os.utime(path, ns=(change.mtime_ns, change.mtime_ns))
    finally:
        temp_path.unlink(missing_ok=True)


def _backup_file(path: Path, codex_home: Path, backup_dir: Path) -> dict:
    relative = _relative_to_home(path, codex_home)
    entry = {"path": relative.as_posix(), "existed": path.is_file()}
    if path.is_file():
        target = backup_dir / "files" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        entry.update(
            {
                "backup": target.relative_to(backup_dir).as_posix(),
                "sha256": sha256_file(path),
                "mtime_ns": path.stat().st_mtime_ns,
            }
        )
    return entry


def create_backup(codex_home: Path, changes: SetupChanges) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = codex_home / "backup-xi-ai" / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    try:
        targets: list[Path] = [changes.config_path, changes.catalog_path]
        targets.extend(change.path for change in changes.rollout_changes)
        file_entries = [_backup_file(path, codex_home, backup_dir) for path in targets]

        database = sqlite_path(codex_home)
        sqlite_entry = {"path": "state_5.sqlite", "existed": False}
        if changes.migrate_sessions and database.is_file():
            snapshot = backup_dir / "db" / "state_5.sqlite"
            backup_sqlite(database, snapshot)
            sqlite_entry = {
                "path": "state_5.sqlite",
                "existed": True,
                "backup": snapshot.relative_to(backup_dir).as_posix(),
                "sha256": sha256_file(snapshot),
            }

        manifest = {
            "version": 1,
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
    except Exception:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise


def _load_manifest(codex_home: Path, backup_dir: Path) -> dict:
    root = (codex_home / "backup-xi-ai").resolve()
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
    if manifest.get("version") != 1 or not isinstance(
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


def _validate_hash(value: object, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise TransactionError(f"备份清单中的 {label} 哈希无效")
    try:
        int(value, 16)
    except ValueError as exc:
        raise TransactionError(f"备份清单中的 {label} 哈希无效") from exc
    return value


def restore_backup(codex_home: Path, backup_dir: Path) -> None:
    manifest = _load_manifest(codex_home, backup_dir)
    restore_entries: list[tuple[dict, Path, Path | None, str | None, int | None]] = []
    seen_targets: set[Path] = set()
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("existed"), bool):
            raise TransactionError("备份清单中的文件元数据无效")
        target = _manifest_target(codex_home, entry.get("path"))
        resolved_target = target.resolve()
        if resolved_target in seen_targets:
            raise TransactionError("备份清单包含重复文件路径")
        seen_targets.add(resolved_target)
        if entry["existed"]:
            source = _resolved_backup_source(backup_dir, entry.get("backup"))
            expected_hash = _validate_hash(entry.get("sha256"), label="file")
            if not source.is_file() or sha256_file(source) != expected_hash:
                raise TransactionError(f"备份文件缺失或损坏：{entry['path']}")
            mtime_ns = entry.get("mtime_ns")
            if not isinstance(mtime_ns, int):
                raise TransactionError("备份清单中的文件时间戳无效")
            restore_entries.append((entry, target, source, expected_hash, mtime_ns))
        else:
            restore_entries.append((entry, target, None, None, None))

    sqlite_entry = manifest.get("sqlite", {})
    sqlite_source: Path | None = None
    sqlite_hash: str | None = None
    if sqlite_entry.get("existed"):
        sqlite_source = _resolved_backup_source(backup_dir, sqlite_entry.get("backup"))
        sqlite_hash = _validate_hash(sqlite_entry.get("sha256"), label="SQLite")
        if not sqlite_source.is_file() or sha256_file(sqlite_source) != sqlite_hash:
            raise TransactionError("SQLite 备份缺失或损坏")
    if sqlite_entry.get("existed") or manifest.get("session_migration"):
        ensure_sqlite_ready(sqlite_path(codex_home))

    for entry, target, source, _, mtime_ns in restore_entries:
        if entry["existed"]:
            assert source is not None
            atomic_write(target, source.read_bytes(), mtime_ns=mtime_ns)
        else:
            target.unlink(missing_ok=True)

    database = sqlite_path(codex_home)
    if sqlite_source is not None:
        atomic_write(database, sqlite_source.read_bytes())
    elif manifest.get("session_migration"):
        database.unlink(missing_ok=True)

    for entry, target, _, expected_hash, _ in restore_entries:
        if entry["existed"]:
            assert expected_hash is not None
            if not target.is_file() or sha256_file(target) != expected_hash:
                raise TransactionError(f"恢复后的文件哈希不匹配：{entry['path']}")
        elif target.exists():
            raise TransactionError(f"恢复时未删除新增文件：{entry['path']}")
    if sqlite_source is not None and (
        not database.is_file() or sha256_file(database) != sqlite_hash
    ):
        raise TransactionError("恢复后的 SQLite 哈希不匹配")


def latest_backup(codex_home: Path) -> Path:
    root = codex_home / "backup-xi-ai"
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
) -> Path:
    codex_home.mkdir(parents=True, exist_ok=True)
    if changes.migrate_sessions:
        ensure_sqlite_ready(
            sqlite_path(codex_home),
            allow_wal_recovery=allow_wal_recovery,
        )
    backup_dir = create_backup(codex_home, changes)
    try:
        atomic_write(changes.config_path, changes.config_content)
        if fail_at == "config":
            raise RuntimeError("injected failure after config")
        atomic_write(changes.catalog_path, changes.catalog_content)
        if fail_at == "catalog":
            raise RuntimeError("injected failure after catalog")
        for change in changes.rollout_changes:
            atomic_rewrite_rollout(change)
        if fail_at == "rollouts":
            raise RuntimeError("injected failure after rollouts")
        if changes.migrate_sessions:
            update_sqlite_provider(sqlite_path(codex_home), PROVIDER_ID)
        if fail_at == "sqlite":
            raise RuntimeError("injected failure after sqlite")
    except Exception as exc:
        try:
            restore_backup(codex_home, backup_dir)
        except Exception as restore_exc:
            raise TransactionError(
                f"配置失败，自动恢复也失败：{restore_exc}"
            ) from exc
        raise TransactionError(f"配置失败，已自动回滚：{exc}") from exc
    return backup_dir
