import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import codex_configurator.transaction as transaction_module
from codex_configurator.errors import (
    BackupSpaceError,
    SessionMigrationError,
    TransactionError,
)
from codex_configurator.progress import ConsoleProgress, ProgressEvent
from codex_configurator.sessions import (
    backup_sqlite,
    collect_rollout_changes,
    ensure_sqlite_ready,
    update_sqlite_provider,
)
from codex_configurator.transaction import (
    SetupChanges,
    apply_setup,
    atomic_rewrite_rollout,
    check_backup_space,
    latest_backup,
    create_backup,
    estimate_backup_space,
    restore_backup,
)


def create_state_database(path: Path, provider: str = "openai"):
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            model_provider TEXT NOT NULL,
            has_user_event INTEGER NOT NULL DEFAULT 0,
            first_user_message TEXT NOT NULL DEFAULT '',
            thread_source TEXT
        )"""
    )
    connection.execute(
        "INSERT INTO threads VALUES ('thread-1', ?, 0, 'hello', NULL)", (provider,)
    )
    connection.commit()
    connection.close()


def create_retained_wal_database(path: Path) -> None:
    source = path.with_name("wal-source.sqlite")
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA wal_autocheckpoint = 0")
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('from-wal')")
        connection.execute(
            """CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                model_provider TEXT NOT NULL,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                first_user_message TEXT NOT NULL DEFAULT '',
                thread_source TEXT
            )"""
        )
        connection.execute(
            "INSERT INTO threads VALUES ('thread-wal', 'openai', 0, 'hello', NULL)"
        )
        connection.commit()
        for suffix in ("", "-wal", "-shm"):
            shutil.copy2(Path(f"{source}{suffix}"), Path(f"{path}{suffix}"))
    finally:
        connection.close()


class SessionTests(unittest.TestCase):
    def test_rollout_scan_progress_is_determinate_and_path_free(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            for index, root_name in enumerate(("sessions", "archived_sessions")):
                rollout = home / root_name / f"rollout-{index}.jsonl"
                rollout.parent.mkdir(parents=True)
                rollout.write_text(
                    '{"type":"session_meta","payload":{"model_provider":"openai"}}\n',
                    encoding="utf-8",
                )
            events = []

            changes = collect_rollout_changes(home, "xi_ai", progress=events.append)

            self.assertEqual(len(changes), 2)
            self.assertEqual(events[0].state, "start")
            self.assertEqual(events[-1].state, "complete")
            self.assertEqual(events[-1].current, 2)
            self.assertEqual(events[-1].total, 2)
            rendered = " ".join(event.label for event in events)
            self.assertNotIn("rollout-", rendered)
            self.assertNotIn(str(home), rendered)

    def test_non_tty_progress_is_throttled_and_has_no_control_characters(self):
        output = []
        reporter = ConsoleProgress(output=output.append, tty=False, percent_step=10)
        for current in range(101):
            reporter(
                ProgressEvent(
                    "bulk", "批量处理", "update", current=current, total=100
                )
            )

        self.assertLessEqual(len(output), 11)
        self.assertTrue(all("\r" not in line and "\x1b" not in line for line in output))

    def test_tty_progress_uses_in_place_updates(self):
        import io

        stream = io.StringIO()
        reporter = ConsoleProgress(stream=stream, tty=True)
        reporter(ProgressEvent("scan", "扫描", "update", current=1, total=2))
        reporter(ProgressEvent("scan", "扫描", "complete", current=2, total=2))
        rendered = stream.getvalue()
        self.assertIn("\r", rendered)
        self.assertIn("#", rendered)
        self.assertTrue(rendered.endswith("\n"))

    def test_rollout_change_only_updates_session_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            rollout = home / "sessions/2026/08/17/rollout-test.jsonl"
            rollout.parent.mkdir(parents=True)
            first = {"type": "session_meta", "payload": {"id": "a", "model_provider": "openai"}}
            second = {"type": "event", "payload": {"message": "keep me"}}
            rollout.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")
            original_mtime = rollout.stat().st_mtime_ns

            changes = collect_rollout_changes(home, "xi_ai")

            self.assertEqual(len(changes), 1)
            lines = changes[0].materialize().decode().splitlines()
            self.assertEqual(json.loads(lines[0])["payload"]["model_provider"], "xi_ai")
            self.assertEqual(json.loads(lines[1]), second)
            self.assertEqual(changes[0].mtime_ns, original_mtime)

    def test_sqlite_provider_and_visibility_are_updated(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "state_5.sqlite"
            create_state_database(database)
            updated = update_sqlite_provider(database, "xi_ai")
            self.assertEqual(updated, 1)
            connection = sqlite3.connect(database)
            row = connection.execute(
                "SELECT model_provider, has_user_event, thread_source FROM threads"
            ).fetchone()
            connection.close()
            self.assertEqual(row, ("xi_ai", 1, "user"))

    def test_visibility_repair_preserves_existing_thread_source(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "state_5.sqlite"
            create_state_database(database, provider="xi_ai")
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE threads SET thread_source = 'cli' WHERE id = 'thread-1'"
            )
            connection.commit()
            connection.close()

            updated = update_sqlite_provider(database, "xi_ai")

            self.assertEqual(updated, 1)
            connection = sqlite3.connect(database)
            row = connection.execute(
                "SELECT model_provider, has_user_event, thread_source FROM threads"
            ).fetchone()
            connection.close()
            self.assertEqual(row, ("xi_ai", 1, "cli"))

    def test_active_wal_database_is_rejected_without_deleting_sidecars(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "state_5.sqlite"
            create_state_database(database)
            wal = Path(f"{database}-wal")
            shm = Path(f"{database}-shm")
            wal.write_bytes(b"wal")
            shm.write_bytes(b"shm")

            with self.assertRaises(SessionMigrationError):
                ensure_sqlite_ready(database)

            self.assertEqual(wal.read_bytes(), b"wal")
            self.assertEqual(shm.read_bytes(), b"shm")

    def test_verified_migration_recovers_valid_retained_wal(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "state_5.sqlite"
            backup = Path(temp) / "backup.sqlite"
            create_retained_wal_database(database)

            ensure_sqlite_ready(database, allow_wal_recovery=True)
            self.assertTrue(backup_sqlite(database, backup))

            connection = sqlite3.connect(backup)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("SELECT value FROM sample").fetchone()[0],
                    "from-wal",
                )
            finally:
                connection.close()

    def test_verified_migration_rejects_active_wal_reader(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "state_5.sqlite"
            writer = sqlite3.connect(database)
            reader = None
            try:
                writer.execute("PRAGMA journal_mode = WAL")
                writer.execute("PRAGMA wal_autocheckpoint = 0")
                writer.execute("CREATE TABLE sample (value TEXT NOT NULL)")
                writer.execute("INSERT INTO sample VALUES ('first')")
                writer.commit()
                reader = sqlite3.connect(database)
                reader.execute("BEGIN")
                reader.execute("SELECT value FROM sample").fetchall()
                writer.execute("INSERT INTO sample VALUES ('second')")
                writer.commit()

                with self.assertRaises(SessionMigrationError):
                    ensure_sqlite_ready(database, allow_wal_recovery=True)

                self.assertEqual(
                    writer.execute("SELECT COUNT(*) FROM sample").fetchone()[0],
                    2,
                )
            finally:
                if reader is not None:
                    reader.rollback()
                    reader.close()
                writer.close()


class TransactionTests(unittest.TestCase):
    def _make_changes(self, home: Path) -> tuple[SetupChanges, dict[Path, bytes]]:
        config = home / "config.toml"
        catalog = home / "xi-ai-model-catalog.json"
        rollout = home / "sessions/2026/08/17/rollout-test.jsonl"
        rollout.parent.mkdir(parents=True)
        config.write_text('model = "old"\n', encoding="utf-8")
        catalog.write_text('{"models":[{"slug":"old"}]}\n', encoding="utf-8")
        rollout.write_text(
            '{"type":"session_meta","payload":{"id":"a","model_provider":"openai"}}\n'
            '{"type":"event","payload":{"message":"must survive rollback"}}\n',
            encoding="utf-8",
        )
        create_state_database(home / "state_5.sqlite")
        originals = {path: path.read_bytes() for path in (config, catalog, rollout)}
        rollout_change = collect_rollout_changes(home, "xi_ai")[0]
        return (
            SetupChanges(
                config_path=config,
                config_content=b'model = "new"\n',
                catalog_path=catalog,
                catalog_content=b'{"models":[{"slug":"new"}]}\n',
                rollout_changes=(rollout_change,),
                migrate_sessions=True,
            ),
            originals,
        )

    def test_each_injected_failure_restores_all_targets(self):
        for fail_at in ("config", "catalog", "rollouts", "sqlite"):
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                changes, originals = self._make_changes(home)

                with self.assertRaises(TransactionError):
                    apply_setup(home, changes, fail_at=fail_at)

                for path, content in originals.items():
                    self.assertEqual(path.read_bytes(), content)
                connection = sqlite3.connect(home / "state_5.sqlite")
                provider = connection.execute(
                    "SELECT model_provider FROM threads"
                ).fetchone()[0]
                connection.close()
                self.assertEqual(provider, "openai")

    def test_transaction_progress_reports_order_and_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            changes, _ = self._make_changes(home)
            events = []

            with self.assertRaises(TransactionError):
                apply_setup(home, changes, fail_at="catalog", progress=events.append)

            phases = [(event.phase, event.state) for event in events]
            self.assertLess(
                phases.index(("backup_files", "start")),
                phases.index(("write_config", "start")),
            )
            self.assertLess(
                phases.index(("write_config", "complete")),
                phases.index(("write_catalog", "start")),
            )
            self.assertIn(("rollback", "start"), phases)
            self.assertIn(("rollback", "complete"), phases)
            rendered = " ".join(event.label for event in events)
            self.assertNotIn(str(home), rendered)
            self.assertNotIn("session_meta", rendered)

    def test_failed_backup_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            changes, _ = self._make_changes(home)
            with patch(
                "codex_configurator.transaction.backup_sqlite",
                side_effect=SessionMigrationError("locked"),
            ):
                with self.assertRaises(SessionMigrationError):
                    create_backup(home, changes)
            backup_root = home / "backup-xi-ai"
            self.assertFalse(list(backup_root.iterdir()))

    def test_compact_backup_preserves_rollout_tail_and_uses_patch_size(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            home.mkdir()
            config = home / "config.toml"
            catalog = home / "xi-ai-model-catalog.json"
            rollout = home / "sessions/2026/08/19/large.jsonl"
            rollout.parent.mkdir(parents=True)
            config.write_bytes(b'model = "old"\r\n')
            catalog.write_bytes(b'{"models":[]}\n')
            tail = b'{"type":"event","payload":{"message":"keep"}}\n' * 2000
            original_first_line = (
                b'{"type":"session_meta","payload":{"model_provider":"openai"}}\n'
            )
            rollout.write_bytes(original_first_line + tail)
            change = collect_rollout_changes(home, "xi_ai")[0]
            changes = SetupChanges(
                config_path=config,
                config_content=b'model = "new"\n',
                catalog_path=catalog,
                catalog_content=b'{"models":[{"slug":"new"}]}\n',
                rollout_changes=(change,),
            )

            backup = apply_setup(home, changes)

            manifest = json.loads((backup / "manifest.json").read_text())
            rollout_entry = next(
                entry
                for entry in manifest["files"]
                if entry["path"].endswith("large.jsonl")
            )
            self.assertEqual(manifest["version"], 2)
            self.assertEqual(rollout_entry["kind"], "rollout_first_line")
            self.assertEqual(
                (backup / rollout_entry["backup"]).read_bytes(),
                original_first_line,
            )
            self.assertFalse((backup / "files" / "sessions").exists())
            backup_bytes = sum(
                path.stat().st_size for path in backup.rglob("*") if path.is_file()
            )
            self.assertLess(backup_bytes, len(tail))

            restore_backup(home, backup)

            self.assertEqual(rollout.read_bytes(), original_first_line + tail)

    def test_external_backup_root_and_latest_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "codex"
            external = root / "other-drive" / "Xi-AI-Backups"
            home.mkdir()
            config = home / "config.toml"
            config.write_bytes(b'model = "old"\n')
            changes = SetupChanges(
                config_path=config,
                config_content=b'model = "new"\n',
                catalog_path=home / "xi-ai-model-catalog.json",
                catalog_content=b'{"models":[]}\n',
            )

            backup = apply_setup(home, changes, backup_root=external)

            self.assertTrue(backup.is_relative_to(external))
            self.assertEqual(latest_backup(home, external), backup)
            restore_backup(home, backup, backup_root=external)
            self.assertEqual(config.read_bytes(), b'model = "old"\n')

    def test_legacy_v1_full_backup_remains_restorable(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / "config.toml"
            config.write_bytes(b'model = "old"\n')
            changes = SetupChanges(
                config_path=config,
                config_content=b'model = "new"\n',
                catalog_path=home / "xi-ai-model-catalog.json",
                catalog_content=b'{"models":[]}\n',
            )
            backup = apply_setup(home, changes)
            manifest_path = backup / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["version"] = 1
            for entry in manifest["files"]:
                entry.pop("kind", None)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            restore_backup(home, backup)

            self.assertEqual(config.read_bytes(), b'model = "old"\n')

    def test_genuine_v1_full_rollout_backup_restores_tail_and_mtime(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / "config.toml"
            rollout = home / "sessions/2026/08/19/legacy.jsonl"
            rollout.parent.mkdir(parents=True)
            original_config = b'model = "old"\n'
            original_rollout = (
                b'{"type":"session_meta","payload":{"model_provider":"openai"}}\n'
                b'{"type":"event","payload":{"message":"legacy tail"}}\n'
            )
            original_mtime = 1_700_000_000_123_456_700
            config.write_bytes(b'model = "new"\n')
            rollout.write_bytes(
                b'{"type":"session_meta","payload":{"model_provider":"xi_ai"}}\n'
                b'{"type":"event","payload":{"message":"legacy tail"}}\n'
            )
            backup = home / "backup-xi-ai" / "20260819-legacy"
            config_source = backup / "files/config.toml"
            rollout_source = backup / "files/sessions/2026/08/19/legacy.jsonl"
            config_source.parent.mkdir(parents=True)
            rollout_source.parent.mkdir(parents=True)
            config_source.write_bytes(original_config)
            rollout_source.write_bytes(original_rollout)
            manifest = {
                "version": 1,
                "created_at": "2026-08-19T00:00:00+00:00",
                "provider": "xi_ai",
                "codex_home": str(home.resolve()),
                "files": [
                    {
                        "path": "config.toml",
                        "existed": True,
                        "backup": "files/config.toml",
                        "sha256": transaction_module.sha256_file(config_source),
                        "mtime_ns": original_mtime,
                    },
                    {
                        "path": "sessions/2026/08/19/legacy.jsonl",
                        "existed": True,
                        "backup": "files/sessions/2026/08/19/legacy.jsonl",
                        "sha256": transaction_module.sha256_file(rollout_source),
                        "mtime_ns": original_mtime,
                    },
                ],
                "sqlite": {"path": "state_5.sqlite", "existed": False},
                "session_migration": False,
            }
            (backup / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            restore_backup(home, backup)

            self.assertEqual(config.read_bytes(), original_config)
            self.assertEqual(rollout.read_bytes(), original_rollout)
            self.assertEqual(rollout.stat().st_mtime_ns, original_mtime)

    def test_v1_manifest_kind_cannot_upgrade_to_compact_semantics(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / "config.toml"
            config.write_bytes(b'model = "old"\n')
            changes = SetupChanges(
                config_path=config,
                config_content=b'model = "new"\n',
                catalog_path=home / "xi-ai-model-catalog.json",
                catalog_content=b'{"models":[]}\n',
            )
            backup = apply_setup(home, changes)
            path = backup / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["version"] = 1
            manifest["files"][0]["kind"] = "rollout_first_line"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            restore_backup(home, backup)

            self.assertEqual(config.read_bytes(), b'model = "old"\n')

    def test_v2_manifest_requires_explicit_kind_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            changes, _ = self._make_changes(home)
            backup = apply_setup(home, changes)
            manifest_path = backup / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            rollout_entry = next(
                entry
                for entry in manifest["files"]
                if entry["path"].endswith("rollout-test.jsonl")
            )
            rollout_entry.pop("kind")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            config_after_setup = changes.config_path.read_bytes()
            rollout_after_setup = changes.rollout_changes[0].path.read_bytes()

            with self.assertRaises(TransactionError):
                restore_backup(home, backup)

            self.assertEqual(changes.config_path.read_bytes(), config_after_setup)
            self.assertEqual(
                changes.rollout_changes[0].path.read_bytes(), rollout_after_setup
            )

    def test_backup_rejects_full_file_that_changes_during_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            changes, originals = self._make_changes(home)
            real_fstat = os.fstat
            calls = 0

            def drifting_fstat(descriptor):
                nonlocal calls
                calls += 1
                result = real_fstat(descriptor)
                if calls == 2:
                    values = list(result)
                    values[6] += 1
                    return os.stat_result(values)
                return result

            with patch(
                "codex_configurator.transaction.os.fstat",
                side_effect=drifting_fstat,
            ):
                with self.assertRaises(TransactionError):
                    create_backup(home, changes)

            self.assertEqual(
                changes.config_path.read_bytes(), originals[changes.config_path]
            )
            self.assertFalse(list((home / "backup-xi-ai").iterdir()))

    def test_backup_rejects_rollout_that_changes_after_tail_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            changes, originals = self._make_changes(home)
            rollout = changes.rollout_changes[0].path
            real_snapshot = transaction_module._snapshot_rollout

            def drifting_snapshot(path):
                result = real_snapshot(path)
                path.write_bytes(path.read_bytes() + b'{"type":"late-event"}\n')
                return result

            with patch(
                "codex_configurator.transaction._snapshot_rollout",
                side_effect=drifting_snapshot,
            ):
                with self.assertRaises(TransactionError):
                    create_backup(home, changes)

            self.assertEqual(
                changes.config_path.read_bytes(), originals[changes.config_path]
            )
            self.assertTrue(rollout.read_bytes().endswith(b'{"type":"late-event"}\n'))
            self.assertFalse(list((home / "backup-xi-ai").iterdir()))

    def test_backup_rejects_missing_planned_rollout(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            changes, _ = self._make_changes(home)
            changes.rollout_changes[0].path.unlink()

            with self.assertRaises(TransactionError):
                create_backup(home, changes)

            self.assertFalse(list((home / "backup-xi-ai").iterdir()))

    def test_atomic_rollout_rewrite_rejects_post_scan_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            changes, _ = self._make_changes(home)
            change = changes.rollout_changes[0]
            drifted = change.path.read_bytes() + b'{"type":"late-event"}\n'
            change.path.write_bytes(drifted)

            with self.assertRaises(TransactionError):
                atomic_rewrite_rollout(change)

            self.assertEqual(change.path.read_bytes(), drifted)

    def test_low_space_is_rejected_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / "config.toml"
            config.write_bytes(b'model = "old"\n')
            changes = SetupChanges(
                config_path=config,
                config_content=b'model = "new"\n',
                catalog_path=home / "xi-ai-model-catalog.json",
                catalog_content=b'{"models":[]}\n',
            )
            usage = shutil.disk_usage(home)
            low = usage._replace(free=0)
            with patch(
                "codex_configurator.transaction.shutil.disk_usage",
                return_value=low,
            ):
                with self.assertRaises(BackupSpaceError):
                    apply_setup(home, changes)
            self.assertEqual(config.read_bytes(), b'model = "old"\n')
            self.assertFalse((home / "xi-ai-model-catalog.json").exists())

    def test_external_volume_space_thresholds_are_checked_independently(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "codex"
            external = root / "external"
            home.mkdir()
            external.mkdir()
            config = home / "config.toml"
            config.write_bytes(b'model = "old"\n')
            changes = SetupChanges(
                config_path=config,
                config_content=b'model = "new"\n',
                catalog_path=home / "xi-ai-model-catalog.json",
                catalog_content=b'{"models":[]}\n',
            )
            estimate = estimate_backup_space(home, changes)
            usage = shutil.disk_usage(home)

            def usage_with(destination_free, home_free):
                def fake_usage(path):
                    resolved = Path(path).resolve()
                    free = destination_free if resolved == external else home_free
                    return usage._replace(free=free)

                return fake_usage

            with patch(
                "codex_configurator.transaction._same_filesystem",
                return_value=False,
            ), patch(
                "codex_configurator.transaction.shutil.disk_usage",
                side_effect=usage_with(
                    estimate.backup_bytes,
                    estimate.local_temp_bytes,
                ),
            ):
                self.assertEqual(
                    check_backup_space(home, changes, external).backup_bytes,
                    estimate.backup_bytes,
                )

            with patch(
                "codex_configurator.transaction._same_filesystem",
                return_value=False,
            ), patch(
                "codex_configurator.transaction.shutil.disk_usage",
                side_effect=usage_with(
                    estimate.backup_bytes - 1,
                    estimate.local_temp_bytes,
                ),
            ):
                with self.assertRaises(BackupSpaceError):
                    check_backup_space(home, changes, external)

            with patch(
                "codex_configurator.transaction._same_filesystem",
                return_value=False,
            ), patch(
                "codex_configurator.transaction.shutil.disk_usage",
                side_effect=usage_with(
                    estimate.backup_bytes,
                    estimate.local_temp_bytes - 1,
                ),
            ):
                with self.assertRaises(BackupSpaceError):
                    check_backup_space(home, changes, external)

    def test_compact_manifest_rejects_non_rollout_and_missing_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / "config.toml"
            config.write_bytes(b'model = "old"\n')
            changes = SetupChanges(
                config_path=config,
                config_content=b'model = "new"\n',
                catalog_path=home / "xi-ai-model-catalog.json",
                catalog_content=b'{"models":[]}\n',
            )
            backup = apply_setup(home, changes)
            path = backup / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["files"][0]["kind"] = "rollout_first_line"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(TransactionError):
                restore_backup(home, backup)

    def test_compact_restore_rejects_corruption_duplicates_and_tail_drift(self):
        for corruption in ("patch", "duplicate", "tail"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                changes, _ = self._make_changes(home)
                backup = apply_setup(home, changes)
                manifest_path = backup / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                rollout_entry = next(
                    entry
                    for entry in manifest["files"]
                    if entry["path"].endswith("rollout-test.jsonl")
                )
                rollout = changes.rollout_changes[0].path
                if corruption == "patch":
                    patch_path = backup / rollout_entry["backup"]
                    damaged = bytearray(patch_path.read_bytes())
                    damaged[0] ^= 1
                    patch_path.write_bytes(bytes(damaged))
                elif corruption == "duplicate":
                    manifest["files"].append(dict(manifest["files"][0]))
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                else:
                    rollout.write_bytes(rollout.read_bytes() + b'{"type":"late"}\n')
                config_after_setup = changes.config_path.read_bytes()
                rollout_after_corruption = rollout.read_bytes()

                with self.assertRaises(TransactionError):
                    restore_backup(home, backup)

                self.assertEqual(changes.config_path.read_bytes(), config_after_setup)
                self.assertEqual(rollout.read_bytes(), rollout_after_corruption)

    def test_verified_setup_backs_up_then_migrates_retained_wal(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / "config.toml"
            catalog = home / "xi-ai-model-catalog.json"
            rollout = home / "sessions/2026/08/19/rollout-test.jsonl"
            rollout.parent.mkdir(parents=True)
            config.write_text('model = "old"\n', encoding="utf-8")
            catalog.write_text('{"models":[{"slug":"old"}]}\n', encoding="utf-8")
            rollout.write_text(
                '{"type":"session_meta","payload":{"model_provider":"openai"}}\n',
                encoding="utf-8",
            )
            create_retained_wal_database(home / "state_5.sqlite")
            changes = SetupChanges(
                config_path=config,
                config_content=b'model = "new"\n',
                catalog_path=catalog,
                catalog_content=b'{"models":[{"slug":"new"}]}\n',
                rollout_changes=tuple(collect_rollout_changes(home, "xi_ai")),
                migrate_sessions=True,
            )

            backup = apply_setup(home, changes, allow_wal_recovery=True)

            migrated = sqlite3.connect(home / "state_5.sqlite")
            original = sqlite3.connect(backup / "db/state_5.sqlite")
            try:
                self.assertEqual(
                    migrated.execute(
                        "SELECT model_provider FROM threads WHERE id = 'thread-wal'"
                    ).fetchone()[0],
                    "xi_ai",
                )
                self.assertEqual(
                    original.execute(
                        "SELECT model_provider FROM threads WHERE id = 'thread-wal'"
                    ).fetchone()[0],
                    "openai",
                )
                self.assertEqual(
                    original.execute("SELECT value FROM sample").fetchone()[0],
                    "from-wal",
                )
            finally:
                migrated.close()
                original.close()

    def test_successful_setup_can_be_restored(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / "config.toml"
            catalog = home / "xi-ai-model-catalog.json"
            config.write_text('model = "old"\n', encoding="utf-8")
            changes = SetupChanges(
                config_path=config,
                config_content=b'model = "new"\n',
                catalog_path=catalog,
                catalog_content=b'{"models":[{"slug":"new"}]}\n',
            )
            backup = apply_setup(home, changes)
            self.assertEqual(config.read_text(), 'model = "new"\n')
            self.assertTrue(catalog.exists())

            restore_backup(home, backup)

            self.assertEqual(config.read_text(), 'model = "old"\n')
            self.assertFalse(catalog.exists())

    def test_restore_rejects_outside_manifest_source_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / "config.toml"
            config.write_text('model = "old"\n', encoding="utf-8")
            changes = SetupChanges(
                config_path=config,
                config_content=b'model = "new"\n',
                catalog_path=home / "xi-ai-model-catalog.json",
                catalog_content=b'{"models":[{"slug":"new"}]}\n',
            )
            backup = apply_setup(home, changes)
            manifest_path = backup / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"][0]["backup"] = "../outside"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaises(TransactionError):
                restore_backup(home, backup)
            self.assertEqual(config.read_text(), 'model = "new"\n')


if __name__ == "__main__":
    unittest.main()
