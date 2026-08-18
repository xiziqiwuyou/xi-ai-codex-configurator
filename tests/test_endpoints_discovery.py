import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codex_configurator.discovery import (
    DesktopProcess,
    ProcessRecord,
    classify_desktop_processes,
    discover,
    discover_running_codex_processes,
    discover_windows_appx_candidates,
    resolve_codex_home,
    resolve_codex_home_details,
)
from codex_configurator.endpoints import API_BASE, MODELS_URL, RESPONSES_URL, api_base_from_origin, resource_url


class EndpointTests(unittest.TestCase):
    def test_fixed_responses_paths(self):
        self.assertEqual(API_BASE, "https://api.xi-ai.cn/v1")
        self.assertEqual(MODELS_URL, "https://api.xi-ai.cn/v1/models")
        self.assertEqual(RESPONSES_URL, "https://api.xi-ai.cn/v1/responses")
        self.assertEqual(api_base_from_origin("https://api.xi-ai.cn/v1/"), API_BASE)
        self.assertEqual(resource_url("responses"), RESPONSES_URL)
        self.assertNotIn("/v1/v1", RESPONSES_URL)


class DiscoveryTests(unittest.TestCase):
    def test_explicit_home_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            expected = Path(temp).resolve()
            actual = resolve_codex_home(temp, env={"CODEX_HOME": "ignored"})
            self.assertEqual(actual, expected)

    def test_environment_home_is_used(self):
        with tempfile.TemporaryDirectory() as temp:
            actual = resolve_codex_home(env={"CODEX_HOME": temp})
            self.assertEqual(actual, Path(temp).resolve())

    def test_default_home(self):
        with tempfile.TemporaryDirectory() as temp:
            actual = resolve_codex_home(env={}, home=Path(temp))
            self.assertEqual(actual, (Path(temp) / ".codex").resolve())

    def test_home_details_report_markers_and_confidence(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            codex_home = home / ".codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text("", encoding="utf-8")
            (codex_home / "sessions").mkdir()

            path, source, markers, confidence = resolve_codex_home_details(
                env={}, home=home
            )

            self.assertEqual(path, codex_home.resolve())
            self.assertEqual(source, "default")
            self.assertEqual(markers, ("config.toml", "sessions"))
            self.assertEqual(confidence, "high")

    def test_only_codex_app_server_process_is_classified(self):
        records = [
            ProcessRecord(
                1,
                "ChatGPT.exe",
                Path("C:/Apps/ChatGPT.exe"),
                "ChatGPT.exe app-server",
            ),
            ProcessRecord(
                2,
                "codex.exe",
                Path("C:/Apps/codex.exe"),
                "codex.exe --version",
            ),
            ProcessRecord(
                3,
                "codex.exe",
                Path("C:/Apps/codex.exe"),
                "codex.exe -c feature=true app-server --flag",
            ),
        ]

        processes = classify_desktop_processes(records, source="test-process")

        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0].pid, 3)

    def test_desktop_root_is_derived_from_same_install_tree(self):
        app = Path("C:/Program Files/WindowsApps/OpenAI.Codex/app")
        records = [
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
                21,
                "ChatGPT.exe",
                app / "ChatGPT.exe",
                '"ChatGPT.exe" --type=renderer',
                parent_pid=20,
            ),
            ProcessRecord(
                30,
                "codex.exe",
                app / "resources/codex.exe",
                '"codex.exe" app-server',
                parent_pid=20,
            ),
            ProcessRecord(
                40,
                "ChatGPT.exe",
                Path("D:/Other/ChatGPT.exe"),
                '"ChatGPT.exe"',
                parent_pid=10,
            ),
        ]

        process = classify_desktop_processes(records, source="test-process")[0]

        self.assertEqual(process.pid, 30)
        self.assertEqual(process.parent_pid, 20)
        self.assertEqual(process.root_pid, 20)
        self.assertEqual(process.root_executable, (app / "ChatGPT.exe").resolve())

    def test_runnable_path_cli_wins_without_losing_desktop_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            cli = home / "bin/codex.exe"
            desktop = home / "store/codex.exe"
            cli.parent.mkdir()
            desktop.parent.mkdir()
            cli.write_bytes(b"")
            desktop.write_bytes(b"")
            process = DesktopProcess(42, desktop, "codex.exe app-server", "test")

            def version_runner(command, **kwargs):
                self.assertEqual(Path(command[0]), cli.resolve())
                return SimpleNamespace(stdout="codex-cli 0.144.1\n", stderr="")

            with patch(
                "codex_configurator.discovery.shutil.which",
                side_effect=lambda name, path=None: str(cli) if name == "codex" else None,
            ):
                result = discover(
                    env={"PATH": str(cli.parent)},
                    home=home,
                    platform="win32",
                    version_runner=version_runner,
                    process_detector=lambda **kwargs: ((process,), ()),
                    appx_detector=lambda **kwargs: ((), ()),
                )

            self.assertEqual(result.executable, cli.resolve())
            self.assertEqual(result.executable_source, "path")
            self.assertEqual(result.desktop_process, process)
            self.assertEqual(result.codex_home, (home / ".codex").resolve())

    def test_inaccessible_store_backend_is_evidence_not_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            desktop = home / "store/codex.exe"
            desktop.parent.mkdir()
            desktop.write_bytes(b"")
            process = DesktopProcess(42, desktop, "codex.exe app-server", "test")

            with patch("codex_configurator.discovery.shutil.which", return_value=None):
                result = discover(
                    env={"PATH": ""},
                    home=home,
                    platform="win32",
                    version_runner=lambda command, **kwargs: (_ for _ in ()).throw(
                        OSError("Access is denied")
                    ),
                    process_detector=lambda **kwargs: ((process,), ()),
                    appx_detector=lambda **kwargs: ((), ()),
                )

            self.assertIsNone(result.executable)
            self.assertEqual(result.desktop_process, process)
            self.assertTrue(any("不可运行" in item for item in result.warnings))

    def test_broken_path_candidate_falls_through_to_npm(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            broken = home / "broken/codex.exe"
            appdata = home / "AppData/Roaming"
            npm = appdata / "npm/codex.cmd"
            broken.parent.mkdir()
            npm.parent.mkdir(parents=True)
            broken.write_bytes(b"")
            npm.write_bytes(b"")

            def version_runner(command, **kwargs):
                command_text = " ".join(str(item) for item in command)
                if str(broken.resolve()) in command_text:
                    raise OSError("not executable")
                self.assertIn(str(npm.resolve()), command_text)
                return SimpleNamespace(stdout="codex-cli 0.144.1", stderr="")

            with patch(
                "codex_configurator.discovery.shutil.which",
                side_effect=lambda name, path=None: str(broken)
                if name == "codex"
                else None,
            ):
                result = discover(
                    env={"PATH": str(broken.parent), "APPDATA": str(appdata)},
                    home=home,
                    platform="win32",
                    version_runner=version_runner,
                    process_detector=lambda **kwargs: ((), ()),
                    appx_detector=lambda **kwargs: ((), ()),
                )

            self.assertEqual(result.executable, npm.resolve())
            self.assertEqual(result.executable_source, "npm")
            self.assertTrue(any("不可运行" in item for item in result.warnings))

    def test_process_backend_can_be_cli_only_after_version_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            desktop = home / "desktop/codex.exe"
            desktop.parent.mkdir()
            desktop.write_bytes(b"")
            process = DesktopProcess(9, desktop, "codex.exe app-server", "test")

            with patch("codex_configurator.discovery.shutil.which", return_value=None):
                result = discover(
                    env={"PATH": ""},
                    home=home,
                    platform="linux",
                    version_runner=lambda command, **kwargs: SimpleNamespace(
                        stdout="codex-cli 0.144.1", stderr=""
                    ),
                    process_detector=lambda **kwargs: ((process,), ()),
                )

            self.assertEqual(result.executable, desktop.resolve())
            self.assertEqual(result.executable_source, "running-process")

    def test_process_inspection_failure_is_non_fatal(self):
        processes, warnings = discover_running_codex_processes(
            platform="win32",
            runner=lambda command, **kwargs: (_ for _ in ()).throw(
                OSError("inspection denied")
            ),
        )

        self.assertEqual(processes, ())
        self.assertEqual(len(warnings), 1)

    def test_windows_appx_path_is_parsed(self):
        expected = Path("C:/Program Files/WindowsApps/OpenAI.Codex/app/resources/codex.exe")
        with patch(
            "codex_configurator.discovery._powershell_executable",
            return_value="powershell.exe",
        ):
            paths, warnings = discover_windows_appx_candidates(
                runner=lambda command, **kwargs: SimpleNamespace(
                    stdout=json.dumps(str(expected)), stderr=""
                )
            )

        self.assertEqual(paths, (expected,))
        self.assertEqual(warnings, ())

    def test_posix_process_path_comes_from_command_arguments(self):
        fake_ps = SimpleNamespace(
            stdout=(
                " 1 0 launchd /sbin/launchd\n"
                " 9 1 Codex /Applications/Codex.app/Contents/MacOS/Codex\n"
                " 12 9 codex /Applications/Codex.app/Contents/Resources/codex "
                "app-server --listen\n"
            ),
            stderr="",
        )

        with patch("codex_configurator.discovery.os.readlink", side_effect=OSError):
            processes, warnings = discover_running_codex_processes(
                platform="darwin",
                runner=lambda command, **kwargs: fake_ps,
            )

        self.assertEqual(warnings, ())
        self.assertEqual(len(processes), 1)
        self.assertEqual(
            processes[0].executable,
            Path("/Applications/Codex.app/Contents/Resources/codex").resolve(),
        )
        self.assertEqual(processes[0].root_pid, 9)


if __name__ == "__main__":
    unittest.main()
