import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_configurator.errors import SessionMigrationError, TransactionError
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
    create_backup,
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
            '{"type":"session_meta","payload":{"id":"a","model_provider":"openai"}}\n',
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
