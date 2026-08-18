import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_configurator.cli import main
from codex_configurator.desktop_control import DesktopCloseResult
from codex_configurator.discovery import DesktopProcess, DiscoveryResult
from codex_configurator.errors import DesktopControlError
from codex_configurator.transaction import apply_setup as apply_setup_transaction


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps({"data": [{"id": "remote-a"}, {"id": "remote-b"}]}).encode()


class CliTests(unittest.TestCase):
    @staticmethod
    def _bundled_catalog():
        return {
            "models": [
                {
                    "slug": "bundled",
                    "display_name": "Bundled",
                    "description": "Bundled",
                    "default_reasoning_level": "low",
                    "supported_reasoning_levels": [
                        {"effort": "low", "description": "Fast"}
                    ],
                    "priority": 1,
                }
            ]
        }

    def test_dry_run_performs_full_prompt_flow_without_writes_or_secret_output(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            answers = iter(["", "2", "n"])
            output = []
            with patch(
                "codex_configurator.cli.discover",
                return_value=DiscoveryResult(home, None, None),
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ):
                result = main(
                    ["setup", "--dry-run", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "super-secret",
                    opener=lambda request, timeout: FakeResponse(),
                    output=output.append,
                )

            self.assertEqual(result, 0)
            self.assertFalse((home / "config.toml").exists())
            self.assertFalse((home / "xi-ai-model-catalog.json").exists())
            rendered = "\n".join(output)
            self.assertIn("默认模型: remote-b", rendered)
            self.assertIn("迁移现有对话: 否", rendered)
            self.assertNotIn("super-secret", rendered)

    def test_detect_only_does_not_prompt_call_api_or_write(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            output = []
            discovery = DiscoveryResult(
                home,
                Path("C:/Tools/codex.exe"),
                "0.144.1",
                executable_source="path",
                codex_home_source="explicit",
                home_confidence="high",
            )
            with patch(
                "codex_configurator.cli.discover", return_value=discovery
            ):
                result = main(
                    ["setup", "--detect-only", "--codex-home", str(home)],
                    input_fn=lambda prompt: (_ for _ in ()).throw(
                        AssertionError("input must not be requested")
                    ),
                    secret_fn=lambda prompt: (_ for _ in ()).throw(
                        AssertionError("token must not be requested")
                    ),
                    opener=lambda request, timeout: (_ for _ in ()).throw(
                        AssertionError("network must not be used")
                    ),
                    output=output.append,
                )

            self.assertEqual(result, 0)
            self.assertFalse((home / "config.toml").exists())
            self.assertFalse((home / "xi-ai-model-catalog.json").exists())
            self.assertIn("未请求 API Key", "\n".join(output))

    def test_no_migration_keeps_sessions_and_database_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            rollout = home / "sessions/2026/08/17/rollout-test.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text(
                '{"type":"session_meta","payload":{"model_provider":"openai"}}\n',
                encoding="utf-8",
            )
            database = home / "state_5.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)"
            )
            connection.commit()
            connection.close()
            before = {rollout: rollout.read_bytes(), database: database.read_bytes()}
            answers = iter(["", "1", "n"])
            output = []
            desktop = DesktopProcess(
                77,
                Path("C:/Apps/Codex/resources/codex.exe"),
                "codex.exe app-server",
                "test",
            )

            with patch(
                "codex_configurator.cli.discover",
                return_value=DiscoveryResult(
                    home, None, None, desktop_process=desktop
                ),
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ):
                result = main(
                    ["setup", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "placeholder-key",
                    opener=lambda request, timeout: FakeResponse(),
                    output=output.append,
                    desktop_closer=lambda process: (_ for _ in ()).throw(
                        AssertionError("N must not close Codex")
                    ),
                )

            self.assertEqual(result, 0)
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)

    def test_migration_requires_detected_codex_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            answers = iter(["", "1", "y"])
            output = []
            with patch(
                "codex_configurator.cli.discover",
                return_value=DiscoveryResult(home, None, None),
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ):
                result = main(
                    ["setup", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "placeholder-key",
                    opener=lambda request, timeout: FakeResponse(),
                    output=output.append,
                )

            self.assertEqual(result, 1)
            self.assertFalse((home / "config.toml").exists())
            self.assertFalse((home / "xi-ai-model-catalog.json").exists())
            self.assertNotIn("placeholder-key", "\n".join(output))

    def test_migration_closes_active_desktop_then_continues(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            database = home / "state_5.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)"
            )
            connection.commit()
            connection.close()
            answers = iter(["", "1", "y"])
            output = []
            closed = []
            desktop = DesktopProcess(
                pid=77,
                executable=Path("C:/Apps/Codex/resources/codex.exe"),
                command_line="codex.exe app-server",
                source="test-process",
                parent_pid=70,
                root_pid=70,
                root_executable=Path("C:/Apps/Codex/Codex.exe"),
            )
            discovery = DiscoveryResult(
                home,
                Path("C:/Tools/codex.exe"),
                "0.144.1",
                executable_source="path",
                desktop_process=desktop,
            )
            with patch(
                "codex_configurator.cli.discover", return_value=discovery
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ):
                result = main(
                    ["setup", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "placeholder-key",
                    opener=lambda request, timeout: FakeResponse(),
                    output=output.append,
                    desktop_closer=lambda process: closed.append(process)
                    or DesktopCloseResult(root_pid=70, forced=False),
                    process_detector=lambda **kwargs: ((), ()),
                )

            self.assertEqual(result, 0)
            self.assertEqual(closed, [desktop])
            self.assertTrue((home / "config.toml").exists())
            self.assertTrue((home / "xi-ai-model-catalog.json").exists())
            rendered = "\n".join(output)
            self.assertIn("PID 77", rendered)
            self.assertIn("正在关闭", rendered)
            self.assertIn("已正常退出", rendered)
            self.assertNotIn("placeholder-key", rendered)

    def test_migration_without_initial_desktop_runs_two_fresh_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            database = home / "state_5.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)"
            )
            connection.commit()
            connection.close()
            answers = iter(["", "1", "y"])
            checks = []
            discovery = DiscoveryResult(
                home,
                Path("C:/Tools/codex.exe"),
                "0.144.1",
            )

            with patch(
                "codex_configurator.cli.discover", return_value=discovery
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ), patch(
                "codex_configurator.cli.apply_setup",
                wraps=apply_setup_transaction,
            ) as setup:
                result = main(
                    ["setup", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "placeholder-key",
                    opener=lambda request, timeout: FakeResponse(),
                    output=lambda value: None,
                    process_detector=lambda **kwargs: checks.append("checked")
                    or ((), ()),
                )

            self.assertEqual(result, 0)
            self.assertEqual(checks, ["checked", "checked"])
            self.assertTrue(setup.call_args.kwargs["allow_wal_recovery"])

    def test_backend_reappearing_before_apply_aborts_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            rollout = home / "sessions/2026/08/19/rollout-test.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text(
                '{"type":"session_meta","payload":{"model_provider":"openai"}}\n',
                encoding="utf-8",
            )
            database = home / "state_5.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)"
            )
            connection.commit()
            connection.close()
            before = {rollout: rollout.read_bytes(), database: database.read_bytes()}
            answers = iter(["", "1", "y"])
            respawned = DesktopProcess(
                88,
                Path("C:/Apps/Codex/resources/codex.exe"),
                "codex.exe app-server",
                "test",
            )
            checks = iter([((), ()), ((respawned,), ())])
            discovery = DiscoveryResult(
                home,
                Path("C:/Tools/codex.exe"),
                "0.144.1",
            )

            with patch(
                "codex_configurator.cli.discover", return_value=discovery
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ):
                result = main(
                    ["setup", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "placeholder-key",
                    opener=lambda request, timeout: FakeResponse(),
                    output=lambda value: None,
                    process_detector=lambda **kwargs: next(checks),
                )

            self.assertEqual(result, 1)
            self.assertFalse((home / "config.toml").exists())
            self.assertFalse((home / "xi-ai-model-catalog.json").exists())
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)

    def test_close_failure_aborts_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            rollout = home / "sessions/2026/08/19/rollout-test.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text(
                '{"type":"session_meta","payload":{"model_provider":"openai"}}\n',
                encoding="utf-8",
            )
            database = home / "state_5.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT)"
            )
            connection.commit()
            connection.close()
            before = {rollout: rollout.read_bytes(), database: database.read_bytes()}
            answers = iter(["", "1", "y"])
            desktop = DesktopProcess(
                77,
                Path("C:/Apps/Codex/resources/codex.exe"),
                "codex.exe app-server",
                "test",
            )
            discovery = DiscoveryResult(
                home,
                Path("C:/Tools/codex.exe"),
                "0.144.1",
                desktop_process=desktop,
            )
            with patch(
                "codex_configurator.cli.discover", return_value=discovery
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ):
                result = main(
                    ["setup", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "placeholder-key",
                    opener=lambda request, timeout: FakeResponse(),
                    output=lambda value: None,
                    desktop_closer=lambda process: (_ for _ in ()).throw(
                        DesktopControlError("关闭失败")
                    ),
                )

            self.assertEqual(result, 1)
            self.assertFalse((home / "config.toml").exists())
            self.assertFalse((home / "xi-ai-model-catalog.json").exists())
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)

    def test_dry_run_reports_close_without_stopping_desktop(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            answers = iter(["", "1", "y"])
            output = []
            desktop = DesktopProcess(
                77,
                Path("C:/Apps/Codex/resources/codex.exe"),
                "codex.exe app-server",
                "test",
            )
            discovery = DiscoveryResult(
                home,
                Path("C:/Tools/codex.exe"),
                "0.144.1",
                desktop_process=desktop,
            )
            with patch(
                "codex_configurator.cli.discover", return_value=discovery
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ):
                result = main(
                    ["setup", "--dry-run", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "placeholder-key",
                    opener=lambda request, timeout: FakeResponse(),
                    output=output.append,
                    desktop_closer=lambda process: (_ for _ in ()).throw(
                        AssertionError("dry-run must not close Codex")
                    ),
                )

            self.assertEqual(result, 0)
            self.assertIn("正式执行时将自动关闭", "\n".join(output))
            self.assertFalse((home / "config.toml").exists())

    def test_respawn_after_close_aborts_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            answers = iter(["", "1", "y"])
            desktop = DesktopProcess(
                77,
                Path("C:/Apps/Codex/resources/codex.exe"),
                "codex.exe app-server",
                "test",
            )
            respawned = DesktopProcess(
                88,
                desktop.executable,
                desktop.command_line,
                "test",
            )
            discovery = DiscoveryResult(
                home,
                Path("C:/Tools/codex.exe"),
                "0.144.1",
                desktop_process=desktop,
            )
            with patch(
                "codex_configurator.cli.discover", return_value=discovery
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ):
                result = main(
                    ["setup", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "placeholder-key",
                    opener=lambda request, timeout: FakeResponse(),
                    output=lambda value: None,
                    desktop_closer=lambda process: DesktopCloseResult(70, False),
                    process_detector=lambda **kwargs: ((respawned,), ()),
                )

            self.assertEqual(result, 1)
            self.assertFalse((home / "config.toml").exists())


if __name__ == "__main__":
    unittest.main()
