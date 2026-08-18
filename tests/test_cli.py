import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_configurator.cli import main
from codex_configurator.discovery import DesktopProcess, DiscoveryResult


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

    def test_migration_rejects_active_desktop_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            answers = iter(["", "1", "y"])
            output = []
            desktop = DesktopProcess(
                pid=77,
                executable=Path("C:/Apps/Codex/resources/codex.exe"),
                command_line="codex.exe app-server",
                source="test-process",
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
                )

            self.assertEqual(result, 1)
            self.assertFalse((home / "config.toml").exists())
            self.assertFalse((home / "xi-ai-model-catalog.json").exists())
            rendered = "\n".join(output)
            self.assertIn("PID 77", rendered)
            self.assertIn("可运行 CLI", rendered)
            self.assertIn("桌面后端", rendered)
            self.assertIn("选择 Y", rendered)
            self.assertIn("重新运行并选择 N", rendered)
            self.assertNotIn("placeholder-key", rendered)


if __name__ == "__main__":
    unittest.main()
