import json
import sqlite3
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_configurator.cli import _choose_context_config, main
from codex_configurator.desktop_control import DesktopCloseResult
from codex_configurator.discovery import DesktopProcess, DiscoveryResult
from codex_configurator.errors import BackupSpaceError, DesktopControlError
from codex_configurator.launcher import CodexLaunchResult
from codex_configurator.toml_merge import (
    CLEAR_CONTEXT,
    CONTEXT_500K,
    PRESERVE_CONTEXT,
)
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

    def test_long_context_menu_writes_selected_preset(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            answers = iter(["", "1", "3", "n"])
            output = []
            with patch(
                "codex_configurator.cli.discover",
                return_value=DiscoveryResult(home, None, None),
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ), patch(
                "codex_configurator.cli.fetch_remote_model_ids",
                return_value=["gpt-5.6-sol"],
            ):
                result = main(
                    ["setup", "--dry-run", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "super-secret",
                    output=output.append,
                )

            self.assertEqual(result, 0)
            rendered = "\n".join(output)
            self.assertIn("上下文配置: 1M 上下文（自动压缩阈值 900K）", rendered)
            self.assertNotIn("super-secret", rendered)

    def test_all_supported_models_offer_long_context_and_retry_invalid_choice(self):
        for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
            with self.subTest(model=model):
                answers = iter(["invalid", "2"])
                output = []
                context = _choose_context_config(
                    model,
                    input_fn=lambda prompt: next(answers),
                    output=output.append,
                )
                self.assertEqual(context, CONTEXT_500K)
                self.assertIn("请输入 1、2、3 或 4。", output)

    def test_long_context_menu_defaults_to_preserve_and_supports_clear(self):
        output = []

        preserved = _choose_context_config(
            "gpt-5.6-sol",
            input_fn=lambda prompt: "",
            output=output.append,
        )
        cleared = _choose_context_config(
            "gpt-5.6-sol",
            input_fn=lambda prompt: "4",
            output=output.append,
        )

        self.assertEqual(preserved, PRESERVE_CONTEXT)
        self.assertEqual(cleared, CLEAR_CONTEXT)
        self.assertTrue(any("272K" in line for line in output))

    def test_long_context_preset_is_written_by_normal_setup(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            answers = iter(["", "1", "2", "n"])
            with patch(
                "codex_configurator.cli.discover",
                return_value=DiscoveryResult(home, None, None),
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ), patch(
                "codex_configurator.cli.fetch_remote_model_ids",
                return_value=["gpt-5.6-luna"],
            ):
                result = main(
                    ["setup", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "super-secret",
                    output=lambda value: None,
                )

            self.assertEqual(result, 0)
            config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual(config["model_context_window"], 500_000)
            self.assertEqual(config["model_auto_compact_token_limit"], 450_000)

    def test_non_long_context_model_does_not_prompt_for_context(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            answers = iter(["", "1", "n"])
            output = []
            with patch(
                "codex_configurator.cli.discover",
                return_value=DiscoveryResult(home, None, None),
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ), patch(
                "codex_configurator.cli.fetch_remote_model_ids",
                return_value=["remote-model"],
            ):
                result = main(
                    ["setup", "--dry-run", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "super-secret",
                    output=output.append,
                )

            self.assertEqual(result, 0)
            rendered = "\n".join(output)
            self.assertIn("上下文配置: 保留现有设置", rendered)
            self.assertNotIn("支持手动长上下文配置", rendered)

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
            self.assertNotIn("扫描本地会话", "\n".join(output))

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
                    codex_launcher=lambda found, *, was_closed: CodexLaunchResult(
                        desktop.root_executable, 9001
                    ),
                )

            self.assertEqual(result, 0)
            self.assertEqual(closed, [desktop])
            self.assertTrue((home / "config.toml").exists())
            self.assertTrue((home / "xi-ai-model-catalog.json").exists())
            rendered = "\n".join(output)
            self.assertIn("PID 77", rendered)
            self.assertIn("正在关闭", rendered)
            self.assertIn("已正常退出", rendered)
            self.assertIn("[扫描本地会话] 完成", rendered)
            self.assertIn("[备份会话数据库] 完成", rendered)
            self.assertIn("[更新会话数据库] 完成", rendered)
            self.assertNotIn("placeholder-key", rendered)

    def test_successful_migration_launches_verified_desktop_root_once(self):
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
            calls = []
            desktop = DesktopProcess(
                pid=77,
                executable=Path("C:/Apps/Codex/resources/codex.exe"),
                command_line="codex.exe app-server",
                source="test-process",
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

            def launcher(found, *, was_closed):
                calls.append((found, was_closed))
                return CodexLaunchResult(desktop.root_executable, 9001)

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
                    desktop_closer=lambda process: DesktopCloseResult(70, False),
                    process_detector=lambda **kwargs: ((), ()),
                    codex_launcher=launcher,
                )

            self.assertEqual(result, 0)
            self.assertEqual(calls, [(discovery, True)])
            self.assertIn("PID 9001", "\n".join(output))

    def test_cli_only_setup_reports_manual_start_without_launching(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            answers = iter(["", "1", "n"])
            output = []
            calls = []
            discovery = DiscoveryResult(
                home,
                Path("/usr/bin/codex"),
                "0.144.1",
                executable_source="path",
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
                    codex_launcher=lambda *args, **kwargs: calls.append(args),
                )

            self.assertEqual(result, 0)
            self.assertEqual(calls, [])
            self.assertIn("手动启动 Codex", "\n".join(output))

    def test_post_commit_launch_failure_returns_nonzero_without_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            answers = iter(["", "1", "n"])
            output = []
            discovery = DiscoveryResult(
                home,
                Path(temp) / "Codex.exe",
                "0.144.1",
                executable_source="explicit",
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
                    codex_launcher=lambda *args, **kwargs: (_ for _ in ()).throw(
                        RuntimeError("launch unavailable")
                    ),
                )

            self.assertEqual(result, 1)
            self.assertTrue((home / "config.toml").exists())
            self.assertIn("配置已提交，但 Codex 启动请求失败", "\n".join(output))

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

    def test_low_space_prompts_for_external_backup_root(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            fallback = Path(temp) / "other-drive" / "Xi-AI-Backups"
            home.mkdir()
            answers = iter(["", "1", "n", str(fallback)])
            output = []
            prompts = []
            checks = []

            def check_space(_home, _changes, root=None):
                checks.append(root)
                if root is None:
                    raise BackupSpaceError("备份空间不足")

            with patch(
                "codex_configurator.cli.discover",
                return_value=DiscoveryResult(home, None, None),
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ), patch(
                "codex_configurator.cli.fetch_remote_model_ids",
                return_value=["remote-model"],
            ), patch(
                "codex_configurator.cli.check_backup_space",
                side_effect=check_space,
            ), patch(
                "codex_configurator.cli.candidate_backup_roots",
                return_value=(fallback,),
            ), patch(
                "codex_configurator.cli.apply_setup",
                return_value=fallback / "20260819-000000-000000",
            ) as apply, patch(
                "codex_configurator.cli.validate_installed",
                return_value={"model": "remote-model"},
            ):
                result = main(
                    ["setup", "--codex-home", str(home)],
                    input_fn=lambda prompt: prompts.append(prompt) or next(answers),
                    secret_fn=lambda prompt: "placeholder-key",
                    output=output.append,
                )

            self.assertEqual(result, 0)
            self.assertEqual(checks, [None, None, fallback])
            self.assertEqual(apply.call_args.kwargs["backup_root"], fallback)
            rendered = "\n".join(output)
            self.assertIn("备份空间不足", rendered)
            self.assertTrue(any("请输入备用备份目录" in prompt for prompt in prompts))
            self.assertNotIn("placeholder-key", rendered)

    def test_low_space_blank_backup_root_aborts_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            answers = iter(["", "1", "n", ""])
            with patch(
                "codex_configurator.cli.discover",
                return_value=DiscoveryResult(home, None, None),
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ), patch(
                "codex_configurator.cli.fetch_remote_model_ids",
                return_value=["remote-model"],
            ), patch(
                "codex_configurator.cli.check_backup_space",
                side_effect=BackupSpaceError("备份空间不足"),
            ), patch(
                "codex_configurator.cli.candidate_backup_roots",
                return_value=(),
            ), patch(
                "codex_configurator.cli.apply_setup",
            ) as apply:
                result = main(
                    ["setup", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "placeholder-key",
                    output=lambda value: None,
                )

            self.assertEqual(result, 1)
            apply.assert_not_called()
            self.assertFalse((home / "config.toml").exists())

    def test_low_space_dry_run_reports_without_prompting_for_backup_root(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            answers = iter(["", "1", "n"])
            output = []
            with patch(
                "codex_configurator.cli.discover",
                return_value=DiscoveryResult(home, None, None),
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ), patch(
                "codex_configurator.cli.fetch_remote_model_ids",
                return_value=["remote-model"],
            ), patch(
                "codex_configurator.cli.check_backup_space",
                side_effect=BackupSpaceError("备份空间不足"),
            ), patch(
                "codex_configurator.cli.candidate_backup_roots",
                side_effect=AssertionError("dry-run must not enumerate backup roots"),
            ), patch(
                "codex_configurator.cli.apply_setup",
                side_effect=AssertionError("dry-run must not create a backup"),
            ):
                result = main(
                    ["setup", "--dry-run", "--codex-home", str(home)],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "placeholder-key",
                    output=output.append,
                )

            self.assertEqual(result, 0)
            self.assertIn("试运行提示：备份空间不足", "\n".join(output))
            self.assertFalse((home / "backup-xi-ai").exists())

    def test_explicit_low_space_root_aborts_without_fallback_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            explicit = Path(temp) / "other-drive" / "Xi-AI-Backups"
            home.mkdir()
            answers = iter(["", "1", "n"])
            with patch(
                "codex_configurator.cli.discover",
                return_value=DiscoveryResult(home, None, None),
            ), patch(
                "codex_configurator.cli.load_bundled_catalog",
                return_value=self._bundled_catalog(),
            ), patch(
                "codex_configurator.cli.fetch_remote_model_ids",
                return_value=["remote-model"],
            ), patch(
                "codex_configurator.cli.check_backup_space",
                side_effect=BackupSpaceError("备份空间不足"),
            ), patch(
                "codex_configurator.cli.candidate_backup_roots",
                side_effect=AssertionError("explicit root must not prompt for fallback"),
            ), patch("codex_configurator.cli.apply_setup") as apply:
                result = main(
                    [
                        "setup",
                        "--codex-home",
                        str(home),
                        "--backup-root",
                        str(explicit),
                    ],
                    input_fn=lambda prompt: next(answers),
                    secret_fn=lambda prompt: "placeholder-key",
                    output=lambda value: None,
                )

            self.assertEqual(result, 1)
            apply.assert_not_called()
            self.assertFalse((home / "config.toml").exists())

    def test_restore_backup_root_selects_latest_external_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "codex"
            root = Path(temp) / "other-drive" / "Xi-AI-Backups"
            backup = root / "20260819-000000-000000"
            home.mkdir()
            with patch(
                "codex_configurator.cli.discover",
                return_value=DiscoveryResult(home, None, None),
            ), patch(
                "codex_configurator.cli.latest_backup",
                return_value=backup,
            ) as latest, patch(
                "codex_configurator.cli.restore_backup",
            ) as restore:
                result = main(
                    [
                        "restore",
                        "--codex-home",
                        str(home),
                        "--backup-root",
                        str(root),
                    ],
                    output=lambda value: None,
                )

            self.assertEqual(result, 0)
            resolved_root = root.resolve()
            latest.assert_called_once_with(home, resolved_root)
            restore.assert_called_once_with(home, backup, backup_root=resolved_root)

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
