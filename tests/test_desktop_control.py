import signal
import unittest
from pathlib import Path
from types import SimpleNamespace

from codex_configurator.desktop_control import close_codex_desktop
from codex_configurator.discovery import ProcessRecord, classify_desktop_processes
from codex_configurator.errors import DesktopControlError


class SnapshotSequence:
    def __init__(self, *snapshots):
        self.snapshots = list(snapshots)
        self.index = 0

    def __call__(self):
        if self.index < len(self.snapshots):
            snapshot = self.snapshots[self.index]
            self.index += 1
            return snapshot
        return self.snapshots[-1]


def windows_records(*, caller_parent=10, backend_path=None, backend_command=None):
    app = Path("C:/Program Files/WindowsApps/OpenAI.Codex/app")
    return [
        ProcessRecord(
            10,
            "explorer.exe",
            Path("C:/Windows/explorer.exe"),
            "explorer.exe",
            parent_pid=1,
        ),
        ProcessRecord(
            20,
            "ChatGPT.exe",
            app / "ChatGPT.exe",
            '"ChatGPT.exe" --remote-debugging-port=1234',
            parent_pid=10,
        ),
        ProcessRecord(
            30,
            "codex.exe",
            backend_path or app / "resources/codex.exe",
            backend_command or '"codex.exe" app-server',
            parent_pid=20,
        ),
        ProcessRecord(
            99,
            "python.exe",
            Path("C:/Python/python.exe"),
            "python setup.py",
            parent_pid=caller_parent,
        ),
    ]


class DesktopControlTests(unittest.TestCase):
    @staticmethod
    def _desktop(records):
        return classify_desktop_processes(records, source="test")[0]

    def test_windows_graceful_close_targets_exact_gui_root(self):
        records = windows_records()
        commands = []

        result = close_codex_desktop(
            self._desktop(records),
            platform="win32",
            caller_pid=99,
            snapshot_fn=SnapshotSequence(records, []),
            action_runner=lambda command, **kwargs: commands.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr=""),
            graceful_timeout=0,
            force_timeout=0,
        )

        self.assertFalse(result.forced)
        self.assertEqual(result.root_pid, 20)
        rendered = " ".join(commands[0])
        self.assertIn("CloseMainWindow", rendered)
        self.assertIn("20", rendered)
        self.assertNotIn("Stop-Process", rendered)

    def test_windows_timeout_revalidates_then_forces_exact_pids(self):
        records = windows_records()
        commands = []

        result = close_codex_desktop(
            self._desktop(records),
            platform="win32",
            caller_pid=99,
            snapshot_fn=SnapshotSequence(records, records, records, []),
            action_runner=lambda command, **kwargs: commands.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr=""),
            graceful_timeout=0,
            force_timeout=0,
        )

        self.assertTrue(result.forced)
        self.assertEqual(len(commands), 2)
        rendered = " ".join(commands[1])
        self.assertIn("Stop-Process", rendered)
        self.assertIn("20,30", rendered)
        self.assertNotIn("-Name", rendered)

    def test_identity_change_is_rejected_before_force(self):
        records = windows_records()
        changed = windows_records(
            backend_path=Path("C:/Temp/codex.exe"),
            backend_command='"codex.exe" app-server --different',
        )
        commands = []

        with self.assertRaisesRegex(DesktopControlError, "身份"):
            close_codex_desktop(
                self._desktop(records),
                platform="win32",
                caller_pid=99,
                snapshot_fn=SnapshotSequence(records, records, changed),
                action_runner=lambda command, **kwargs: commands.append(command)
                or SimpleNamespace(returncode=0, stdout="", stderr=""),
                graceful_timeout=0,
                force_timeout=0,
            )

        self.assertEqual(len(commands), 1)

    def test_orphaned_backend_is_forced_without_reusing_old_root_pid(self):
        records = windows_records()
        orphaned = [record for record in records if record.pid not in {20}]
        commands = []

        result = close_codex_desktop(
            self._desktop(records),
            platform="win32",
            caller_pid=99,
            snapshot_fn=SnapshotSequence(records, orphaned, orphaned, []),
            action_runner=lambda command, **kwargs: commands.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr=""),
            graceful_timeout=0,
            force_timeout=0,
        )

        self.assertTrue(result.forced)
        rendered = " ".join(commands[1])
        self.assertIn("$ids=@(30)", rendered)
        self.assertNotIn("20,30", rendered)

    def test_running_inside_codex_tree_is_rejected_before_signal(self):
        records = windows_records(caller_parent=30)
        commands = []

        with self.assertRaisesRegex(DesktopControlError, "系统 PowerShell"):
            close_codex_desktop(
                self._desktop(records),
                platform="win32",
                caller_pid=99,
                snapshot_fn=SnapshotSequence(records),
                action_runner=lambda command, **kwargs: commands.append(command),
            )

        self.assertEqual(commands, [])

    def test_windows_close_permission_failure_is_reported(self):
        records = windows_records()

        with self.assertRaisesRegex(DesktopControlError, "权限"):
            close_codex_desktop(
                self._desktop(records),
                platform="win32",
                caller_pid=99,
                snapshot_fn=SnapshotSequence(records),
                action_runner=lambda command, **kwargs: SimpleNamespace(
                    returncode=1, stdout="", stderr="Access denied"
                ),
            )

    def test_force_timeout_is_reported(self):
        records = windows_records()
        commands = []

        with self.assertRaisesRegex(DesktopControlError, "仍在运行"):
            close_codex_desktop(
                self._desktop(records),
                platform="win32",
                caller_pid=99,
                snapshot_fn=SnapshotSequence(
                    records, records, records, records
                ),
                action_runner=lambda command, **kwargs: commands.append(command)
                or SimpleNamespace(returncode=0, stdout="", stderr=""),
                graceful_timeout=0,
                force_timeout=0,
            )

        self.assertEqual(len(commands), 2)

    def test_snapshot_failure_is_reported_as_desktop_control_error(self):
        records = windows_records()
        calls = 0

        def failing_snapshot():
            nonlocal calls
            calls += 1
            if calls == 1:
                return records
            raise OSError("process table unavailable")

        with self.assertRaisesRegex(DesktopControlError, "重新检查"):
            close_codex_desktop(
                self._desktop(records),
                platform="win32",
                caller_pid=99,
                snapshot_fn=failing_snapshot,
                action_runner=lambda command, **kwargs: SimpleNamespace(
                    returncode=0, stdout="", stderr=""
                ),
                graceful_timeout=0,
            )

    def test_posix_uses_term_then_kill_for_exact_processes(self):
        app = Path("/Applications/Codex.app/Contents")
        records = [
            ProcessRecord(1, "launchd", Path("/sbin/launchd"), "launchd", 0),
            ProcessRecord(20, "Codex", app / "MacOS/Codex", "Codex", 1),
            ProcessRecord(
                30,
                "codex",
                app / "Resources/codex",
                "codex app-server",
                20,
            ),
            ProcessRecord(99, "python3", Path("/usr/bin/python3"), "python3", 1),
        ]
        sent = []

        result = close_codex_desktop(
            self._desktop(records),
            platform="darwin",
            caller_pid=99,
            snapshot_fn=SnapshotSequence(records, records, records, []),
            signal_sender=lambda pid, value: sent.append((pid, value)),
            graceful_timeout=0,
            force_timeout=0,
        )

        self.assertTrue(result.forced)
        self.assertEqual(
            sent,
            [
                (20, getattr(signal, "SIGTERM", 15)),
                (20, getattr(signal, "SIGKILL", 9)),
                (30, getattr(signal, "SIGKILL", 9)),
            ],
        )

    def test_posix_force_allows_backend_to_exit_with_root(self):
        app = Path("/Applications/Codex.app/Contents")
        records = [
            ProcessRecord(1, "launchd", Path("/sbin/launchd"), "launchd", 0),
            ProcessRecord(20, "Codex", app / "MacOS/Codex", "Codex", 1),
            ProcessRecord(
                30,
                "codex",
                app / "Resources/codex",
                "codex app-server",
                20,
            ),
            ProcessRecord(99, "python3", Path("/usr/bin/python3"), "python3", 1),
        ]

        def send_signal(pid, sent_signal):
            if sent_signal == getattr(signal, "SIGKILL", 9) and pid == 30:
                raise ProcessLookupError(pid)

        result = close_codex_desktop(
            self._desktop(records),
            platform="darwin",
            caller_pid=99,
            snapshot_fn=SnapshotSequence(records, records, records, []),
            signal_sender=send_signal,
            graceful_timeout=0,
            force_timeout=0,
        )

        self.assertTrue(result.forced)


if __name__ == "__main__":
    unittest.main()
