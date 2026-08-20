import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codex_configurator.discovery import DesktopProcess, DiscoveryResult
from codex_configurator.errors import LaunchError
from codex_configurator.launcher import (
    CodexLaunchResult,
    _registered_codex_app_id,
    launch_codex,
    select_launch_target,
)


class LauncherTests(unittest.TestCase):
    def test_registered_app_id_is_validated(self):
        with patch(
            "codex_configurator.launcher.shutil.which",
            return_value="powershell.exe",
        ):
            valid = _registered_codex_app_id(
                runner=lambda command, **kwargs: SimpleNamespace(
                    returncode=0,
                    stdout="OpenAI.Codex_2p2nqsd0c76g0!App\n",
                )
            )
            invalid = _registered_codex_app_id(
                runner=lambda command, **kwargs: SimpleNamespace(
                    returncode=0,
                    stdout="bad;value\n",
                )
            )

        self.assertEqual(valid, "OpenAI.Codex_2p2nqsd0c76g0!App")
        self.assertIsNone(invalid)

    def test_closed_desktop_prefers_verified_root(self):
        process = DesktopProcess(
            pid=77,
            executable=Path("/opt/Codex/resources/codex"),
            command_line="codex app-server",
            source="test",
            root_executable=Path("/opt/Codex/Codex"),
        )
        discovery = DiscoveryResult(
            Path("/tmp/codex"), None, None, desktop_process=process
        )

        self.assertEqual(
            select_launch_target(discovery, was_closed=True),
            Path("/opt/Codex/Codex"),
        )

    def test_closed_desktop_falls_back_to_backend(self):
        process = DesktopProcess(
            pid=77,
            executable=Path("/opt/Codex/resources/codex"),
            command_line="codex app-server",
            source="test",
        )
        discovery = DiscoveryResult(
            Path("/tmp/codex"), None, None, desktop_process=process
        )

        self.assertEqual(
            select_launch_target(discovery, was_closed=True),
            Path("/opt/Codex/resources/codex"),
        )

    def test_running_desktop_and_cli_only_discovery_are_not_launchable(self):
        process = DesktopProcess(
            pid=77,
            executable=Path("/opt/Codex/resources/codex"),
            command_line="codex app-server",
            source="test",
        )
        self.assertIsNone(
            select_launch_target(
                DiscoveryResult(
                    Path("/tmp/codex"),
                    Path("/usr/bin/codex"),
                    "0.144.1",
                    executable_source="path",
                    desktop_process=process,
                ),
                was_closed=False,
            )
        )
        self.assertIsNone(
            select_launch_target(
                DiscoveryResult(
                    Path("/tmp/codex"),
                    Path("/usr/bin/codex"),
                    "0.144.1",
                    executable_source="path",
                ),
                was_closed=False,
            )
        )

    def test_launch_uses_detached_windows_invocation(self):
        calls = []
        discovery = DiscoveryResult(
            Path("C:/Users/test/.codex"),
            Path("C:/Program Files/Codex/Codex.exe"),
            "0.144.1",
            executable_source="desktop-install",
        )

        result = launch_codex(
            discovery,
            was_closed=False,
            platform="win32",
            runner=lambda command, **kwargs: calls.append((command, kwargs))
            or SimpleNamespace(pid=123),
        )

        self.assertEqual(
            result,
            CodexLaunchResult(Path("C:/Program Files/Codex/Codex.exe"), 123),
        )
        command, kwargs = calls[0]
        self.assertEqual(command, [str(Path("C:/Program Files/Codex/Codex.exe"))])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["creationflags"], 0x00000008 | 0x00000200)
        self.assertNotIn("start_new_session", kwargs)

    def test_store_target_uses_registered_app_shell_activation(self):
        calls = []
        discovery = DiscoveryResult(
            Path("C:/Users/test/.codex"),
            Path(
                "C:/Program Files/WindowsApps/"
                "OpenAI.Codex_26.721.4979.0_x64__2p2nqsd0c76g0/"
                "app/resources/codex.exe"
            ),
            "0.144.1",
            executable_source="desktop-install",
        )

        with patch(
            "codex_configurator.launcher.shutil.which",
            return_value="explorer.exe",
        ):
            result = launch_codex(
                discovery,
                was_closed=False,
                platform="win32",
                registered_app_id_fn=lambda: "OpenAI.Codex_2p2nqsd0c76g0!App",
                runner=lambda command, **kwargs: calls.append((command, kwargs))
                or SimpleNamespace(pid=321),
            )

        self.assertEqual(result.pid, 321)
        self.assertEqual(
            calls[0][0],
            ["explorer.exe", "shell:AppsFolder\\OpenAI.Codex_2p2nqsd0c76g0!App"],
        )

    def test_invalid_injected_app_id_does_not_reach_shell_command(self):
        calls = []
        discovery = DiscoveryResult(
            Path("C:/Users/test/.codex"),
            Path("C:/Program Files/WindowsApps/OpenAI.Codex/app/resources/codex.exe"),
            "0.144.1",
            executable_source="desktop-install",
        )

        with patch("codex_configurator.launcher.shutil.which", return_value="explorer.exe"):
            launch_codex(
                discovery,
                was_closed=False,
                platform="win32",
                registered_app_id_fn=lambda: "bad;id",
                runner=lambda command, **kwargs: calls.append((command, kwargs))
                or SimpleNamespace(pid=322),
            )

        self.assertEqual(calls[0][0], [str(discovery.executable)])

    def test_launch_uses_new_session_on_posix(self):
        calls = []
        discovery = DiscoveryResult(
            Path("/tmp/.codex"),
            Path("/Applications/Codex.app/Contents/MacOS/codex"),
            "0.144.1",
            executable_source="explicit",
        )

        result = launch_codex(
            discovery,
            was_closed=False,
            platform="darwin",
            runner=lambda command, **kwargs: calls.append((command, kwargs))
            or SimpleNamespace(pid=456),
        )

        self.assertEqual(result.pid, 456)
        self.assertTrue(calls[0][1]["start_new_session"])
        self.assertNotIn("creationflags", calls[0][1])

    def test_launch_failure_is_normalized(self):
        discovery = DiscoveryResult(
            Path("/tmp/.codex"),
            Path("/Applications/Codex.app/Contents/MacOS/codex"),
            "0.144.1",
            executable_source="desktop-install",
        )

        with self.assertRaises(LaunchError):
            launch_codex(
                discovery,
                was_closed=False,
                runner=lambda command, **kwargs: (_ for _ in ()).throw(
                    OSError("permission denied")
                ),
            )


if __name__ == "__main__":
    unittest.main()
