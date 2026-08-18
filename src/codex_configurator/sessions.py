from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .errors import SessionMigrationError


@dataclass(frozen=True)
class RolloutChange:
    path: Path
    updated_first_line: bytes
    mtime_ns: int

    def materialize(self) -> bytes:
        with self.path.open("rb") as handle:
            handle.readline()
            return self.updated_first_line + handle.read()


def _update_session_meta(line: bytes, target_provider: str) -> bytes | None:
    newline = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
    raw = line[: -len(newline)] if newline else line
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if document.get("type") != "session_meta" or not isinstance(document.get("payload"), dict):
        return None
    payload = document["payload"]
    if payload.get("model_provider") == target_provider:
        return None
    payload["model_provider"] = target_provider
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return encoded + newline


def collect_rollout_changes(codex_home: Path, target_provider: str) -> list[RolloutChange]:
    changes: list[RolloutChange] = []
    roots = [codex_home / "sessions", codex_home / "archived_sessions"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                with path.open("rb") as handle:
                    updated = _update_session_meta(handle.readline(), target_provider)
            except OSError as exc:
                raise SessionMigrationError(f"无法读取会话记录文件：{path}") from exc
            if updated is not None:
                changes.append(
                    RolloutChange(
                        path=path,
                        updated_first_line=updated,
                        mtime_ns=path.stat().st_mtime_ns,
                    )
                )
    return changes


def sqlite_path(codex_home: Path) -> Path:
    return codex_home / "state_5.sqlite"


def sqlite_columns(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        return {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
    except sqlite3.Error as exc:
        raise SessionMigrationError("无法检查 Codex 会话数据库") from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass


def backup_sqlite(path: Path, destination: Path) -> bool:
    if not path.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA busy_timeout = 3000")
        connection.execute("VACUUM INTO ?", (str(destination),))
    except sqlite3.Error as exc:
        destination.unlink(missing_ok=True)
        raise SessionMigrationError(
            "无法备份 state_5.sqlite；请完全退出 Codex 后重试"
        ) from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
    return True


def ensure_sqlite_ready(path: Path) -> None:
    if not path.is_file():
        return
    sidecars = (Path(f"{path}-wal"), Path(f"{path}-shm"))
    if any(sidecar.exists() for sidecar in sidecars):
        raise SessionMigrationError(
            "Codex 会话数据库正在使用中；请完全退出 Codex 后重试"
        )
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA busy_timeout = 3000")
        connection.execute("BEGIN EXCLUSIVE")
        connection.rollback()
    except sqlite3.Error as exc:
        raise SessionMigrationError(
            "Codex 会话数据库正在使用中；请完全退出 Codex 后重试"
        ) from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass


def update_sqlite_provider(path: Path, target_provider: str) -> int:
    if not path.is_file():
        return 0
    columns = sqlite_columns(path)
    if "model_provider" not in columns:
        raise SessionMigrationError("Codex 会话数据库缺少 model_provider 列")
    assignments = ["model_provider = ?"]
    parameters: list[object] = [target_provider]
    if {"has_user_event", "first_user_message"}.issubset(columns):
        assignments.append(
            "has_user_event = CASE WHEN COALESCE(TRIM(first_user_message), '') <> '' THEN 1 ELSE has_user_event END"
        )
    if {"thread_source", "first_user_message"}.issubset(columns):
        assignments.append(
            "thread_source = CASE WHEN COALESCE(thread_source, '') = '' AND COALESCE(first_user_message, '') <> '' THEN 'user' ELSE thread_source END"
        )
    predicates = ["COALESCE(model_provider, '') <> ?"]
    if {"has_user_event", "first_user_message"}.issubset(columns):
        predicates.append(
            "(COALESCE(first_user_message, '') <> '' AND COALESCE(has_user_event, 0) <> 1)"
        )
    if {"thread_source", "first_user_message"}.issubset(columns):
        predicates.append(
            "(COALESCE(first_user_message, '') <> '' AND COALESCE(thread_source, '') = '')"
        )
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA busy_timeout = 3000")
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            f"UPDATE threads SET {', '.join(assignments)} WHERE {' OR '.join(predicates)}",
            (*parameters, target_provider),
        )
        updated = cursor.rowcount
        connection.commit()
        return updated
    except sqlite3.Error as exc:
        try:
            if connection is not None:
                connection.rollback()
        except Exception:
            pass
        raise SessionMigrationError(
            "无法更新 Codex 会话数据库；请完全退出 Codex 后重试"
        ) from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
