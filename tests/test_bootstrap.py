import hashlib
import io
import json
import stat
import tempfile
import unittest
import warnings
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

from scripts import bootstrap, package_release


class FakeResponse:
    def __init__(self, payload: bytes, *, content_length: bool = True):
        self._stream = io.BytesIO(payload)
        self.headers = (
            {"Content-Length": str(len(payload))} if content_length else {}
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        return self._stream.read(size)


def release_bundle() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("src/codex_configurator/__main__.py", "")
        archive.writestr("assets/bundled-models.json", '{"models": []}')
        archive.writestr("scripts/setup.ps1", "")
        archive.writestr("scripts/setup.sh", "")
    return stream.getvalue()


class BootstrapTests(unittest.TestCase):
    def test_known_length_progress_is_monotonic_and_completes_at_100_percent(self):
        payload = b"x" * (2 * 1024 * 1024 + 7)
        events = []

        result = bootstrap._read_limited(
            FakeResponse(payload),
            len(payload) + 1,
            progress=events.append,
            stage="程序包",
        )

        updates = [event for event in events if event.state == "update"]
        self.assertEqual(result, payload)
        self.assertEqual(events[0].state, "start")
        self.assertEqual(events[-1].state, "complete")
        self.assertEqual(events[-1].current, len(payload))
        self.assertEqual(events[-1].total, len(payload))
        self.assertEqual(
            [event.current for event in updates],
            sorted(event.current for event in updates),
        )

        output = io.StringIO()
        reporter = bootstrap.BootstrapProgress(stream=output, tty=False)
        for event in events:
            reporter(event)
        self.assertIn("100%", output.getvalue())
        self.assertNotIn("\r", output.getvalue())

    def test_unknown_length_progress_reports_bytes_without_percentage(self):
        events = []
        payload = b"x" * 2048

        bootstrap._read_limited(
            FakeResponse(payload, content_length=False),
            4096,
            progress=events.append,
            stage="未知长度",
        )

        self.assertTrue(all(event.total is None for event in events))
        output = io.StringIO()
        reporter = bootstrap.BootstrapProgress(stream=output, tty=False)
        for event in events:
            reporter(event)
        rendered = output.getvalue()
        self.assertIn("已下载", rendered)
        self.assertNotIn("%", rendered)

    def test_retry_progress_resets_the_next_attempt(self):
        events = []
        attempts = []

        def opener(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) == 1:
                raise URLError("temporary reset")
            return FakeResponse(b"ok")

        with patch.object(bootstrap.time, "sleep"):
            bootstrap._open_bytes(
                "https://api.github.com/example",
                opener=opener,
                limit=1024,
                progress=events.append,
                stage="重试下载",
            )

        retry = next(event for event in events if event.state == "retry")
        second_start = next(
            event
            for event in events
            if event.state == "start" and event.attempt == 2
        )
        self.assertEqual(retry.current, 0)
        self.assertEqual(retry.attempt, 2)
        self.assertEqual(second_start.current, 0)

    def test_progress_starts_before_the_network_opener(self):
        events = []

        def opener(request, timeout):
            self.assertEqual(events[-1].state, "start")
            self.assertEqual(events[-1].current, 0)
            return FakeResponse(b"ok")

        bootstrap._open_bytes(
            "https://api.github.com/example",
            opener=opener,
            limit=1024,
            progress=events.append,
            stage="连接 GitHub",
        )

        self.assertEqual(events[-1].state, "complete")

    def test_short_content_length_response_is_rejected(self):
        response = FakeResponse(b"short")
        response.headers["Content-Length"] = "10"
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap._read_limited(response, 1024)

    def test_tty_progress_uses_in_place_updates_and_finishes_with_newline(self):
        output = io.StringIO()
        reporter = bootstrap.BootstrapProgress(stream=output, tty=True)
        reporter(
            bootstrap.DownloadProgress(
                "程序包", "update", current=50, total=100
            )
        )
        reporter(
            bootstrap.DownloadProgress(
                "程序包", "complete", current=100, total=100
            )
        )
        rendered = output.getvalue()
        self.assertIn("\r", rendered)
        self.assertIn("#", rendered)
        self.assertTrue(rendered.endswith("\n"))

    def test_retry_renderer_does_not_keep_previous_percentage_bucket(self):
        output = io.StringIO()
        reporter = bootstrap.BootstrapProgress(
            stream=output, tty=False, percent_step=10
        )
        reporter(
            bootstrap.DownloadProgress(
                "程序包", "update", current=90, total=100, attempt=1
            )
        )
        reporter(
            bootstrap.DownloadProgress(
                "程序包", "retry", current=0, total=100, attempt=2
            )
        )
        reporter(
            bootstrap.DownloadProgress(
                "程序包", "start", current=0, total=100, attempt=2
            )
        )
        reporter(
            bootstrap.DownloadProgress(
                "程序包", "update", current=10, total=100, attempt=2
            )
        )
        self.assertIn(" 10%", output.getvalue())

    def test_validated_cache_reports_skip_without_redownloading_bundle(self):
        bundle = release_bundle()
        checksum = hashlib.sha256(bundle).hexdigest()
        release_url = "https://api.github.com/repos/owner/repo/releases/tags/v1"
        bundle_url = (
            "https://github.com/owner/repo/releases/download/v1/"
            "xi-ai-codex-bundle.zip"
        )
        checksum_url = bundle_url + ".sha256"
        release = json.dumps(
            {
                "tag_name": "v1",
                "assets": [
                    {
                        "name": bootstrap.BUNDLE_NAME,
                        "browser_download_url": bundle_url,
                    },
                    {
                        "name": bootstrap.CHECKSUM_NAME,
                        "browser_download_url": checksum_url,
                    },
                ],
            }
        ).encode()
        payloads = {
            release_url: release,
            bundle_url: bundle,
            checksum_url: f"{checksum}  {bootstrap.BUNDLE_NAME}\n".encode(),
        }
        requests = []

        def opener(request, timeout):
            requests.append(request.full_url)
            return FakeResponse(payloads[request.full_url])

        with tempfile.TemporaryDirectory() as temp:
            bootstrap.install_release("owner/repo", "v1", Path(temp), opener=opener)
            requests.clear()
            events = []

            bootstrap.install_release(
                "owner/repo",
                "v1",
                Path(temp),
                opener=opener,
                progress=events.append,
            )

        self.assertNotIn(bundle_url, requests)
        self.assertTrue(
            any(
                event.stage == "已验证缓存，跳过程序包下载"
                and event.state == "complete"
                for event in events
            )
        )

    def test_unsupported_python_stops_before_github_request(self):
        with redirect_stderr(io.StringIO()):
            with patch.object(bootstrap.sys, "version_info", (3, 10, 9)):
                result = bootstrap.main(
                    ["--repo", "owner/repo", "--version", "v1.0.0"],
                    opener=lambda request, timeout: (_ for _ in ()).throw(
                        AssertionError("GitHub must not be called")
                    ),
                )

        self.assertEqual(result, 1)

    def test_transient_github_failures_are_retried(self):
        attempts = []

        def opener(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) < 3:
                raise URLError("temporary reset")
            return FakeResponse(b"ok")

        with patch.object(bootstrap.time, "sleep") as sleeper:
            payload = bootstrap._open_bytes(
                "https://api.github.com/example", opener=opener, limit=1024
            )

        self.assertEqual(payload, b"ok")
        self.assertEqual(len(attempts), 3)
        self.assertEqual([call.args[0] for call in sleeper.call_args_list], [0.5, 1.0])

    def test_verified_release_defaults_to_detect_only(self):
        bundle = release_bundle()
        checksum = hashlib.sha256(bundle).hexdigest()
        release_url = "https://api.github.com/repos/owner/repo/releases/tags/v1.0.0"
        bundle_url = (
            "https://github.com/owner/repo/releases/download/v1.0.0/"
            "xi-ai-codex-bundle.zip"
        )
        checksum_url = bundle_url + ".sha256"
        release = json.dumps(
            {
                "tag_name": "v1.0.0",
                "assets": [
                    {
                        "name": bootstrap.BUNDLE_NAME,
                        "browser_download_url": bundle_url,
                    },
                    {
                        "name": bootstrap.CHECKSUM_NAME,
                        "browser_download_url": checksum_url,
                    },
                ],
            }
        ).encode()
        payloads = {
            release_url: release,
            bundle_url: bundle,
            checksum_url: f"{checksum}  {bootstrap.BUNDLE_NAME}\n".encode(),
        }
        commands = []

        def opener(request, timeout):
            return FakeResponse(payloads[request.full_url])

        def runner(command, **kwargs):
            commands.append((command, kwargs))
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            result = bootstrap.main(
                [
                    "--repo",
                    "owner/repo",
                    "--version",
                    "v1.0.0",
                    "--cache-dir",
                    temp,
                ],
                opener=opener,
                runner=runner,
            )
            cache = Path(temp) / "v1.0.0"
            cache_exists = (cache / "src/codex_configurator/__main__.py").is_file()
            marker = (cache / ".release-sha256").read_text().strip()

        self.assertEqual(result, 0)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0][-2:], ["setup", "--detect-only"])
        self.assertTrue(cache_exists)
        self.assertEqual(marker, checksum)

    def test_configure_flag_does_not_force_detect_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in bootstrap.REQUIRED_PATHS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            commands = []

            with patch.object(
                bootstrap,
                "install_release",
                return_value=("v1.0.0", root),
            ), redirect_stdout(io.StringIO()):
                result = bootstrap.main(
                    ["--repo", "owner/repo", "--version", "v1.0.0", "--configure"],
                    runner=lambda command, **kwargs: commands.append(command)
                    or SimpleNamespace(returncode=0),
                )

        self.assertEqual(result, 0)
        self.assertEqual(commands[0][-1], "setup")
        self.assertNotIn("--detect-only", commands[0])

    def test_checksum_mismatch_stops_before_execution(self):
        bundle = release_bundle()
        release_url = "https://api.github.com/repos/owner/repo/releases/tags/v1"
        bundle_url = (
            "https://github.com/owner/repo/releases/download/v1/"
            "xi-ai-codex-bundle.zip"
        )
        checksum_url = bundle_url + ".sha256"
        payloads = {
            release_url: json.dumps(
                {
                    "tag_name": "v1",
                    "assets": [
                        {
                            "name": bootstrap.BUNDLE_NAME,
                            "browser_download_url": bundle_url,
                        },
                        {
                            "name": bootstrap.CHECKSUM_NAME,
                            "browser_download_url": checksum_url,
                        },
                    ],
                }
            ).encode(),
            bundle_url: bundle,
            checksum_url: ("0" * 64 + "  bundle.zip\n").encode(),
        }
        called = False

        def runner(command, **kwargs):
            nonlocal called
            called = True
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as temp, redirect_stderr(
            io.StringIO()
        ), redirect_stdout(io.StringIO()):
            result = bootstrap.main(
                ["--repo", "owner/repo", "--version", "v1", "--cache-dir", temp],
                opener=lambda request, timeout: FakeResponse(
                    payloads[request.full_url]
                ),
                runner=runner,
            )

        self.assertEqual(result, 1)
        self.assertFalse(called)

    def test_unsafe_zip_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / "bundle.zip"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("../outside.txt", "bad")

            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.safe_extract(bundle, root / "extract")

    def test_symbolic_link_and_duplicate_zip_entries_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            symlink_bundle = root / "symlink.zip"
            link = zipfile.ZipInfo("link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(symlink_bundle, "w") as archive:
                archive.writestr(link, "target")
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.safe_extract(symlink_bundle, root / "symlink-out")

            duplicate_bundle = root / "duplicate.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(duplicate_bundle, "w") as archive:
                    archive.writestr("same.txt", "one")
                    archive.writestr("same.txt", "two")
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.safe_extract(duplicate_bundle, root / "duplicate-out")

            case_collision_bundle = root / "case-collision.zip"
            with zipfile.ZipFile(case_collision_bundle, "w") as archive:
                archive.writestr("src/file.py", "one")
                archive.writestr("SRC/file.py", "two")
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.safe_extract(
                    case_collision_bundle, root / "case-collision-out"
                )

    def test_checksum_must_name_the_expected_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            checksum = Path(temp) / bootstrap.CHECKSUM_NAME
            checksum.write_text(
                f"{'0' * 64}  another-file.zip\n", encoding="ascii"
            )

            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._parse_checksum(checksum)

    def test_release_version_is_required(self):
        with redirect_stderr(io.StringIO()):
            result = bootstrap.main(["--repo", "owner/repo"])

        self.assertEqual(result, 1)


class PackageReleaseTests(unittest.TestCase):
    def test_release_package_contains_runtime_and_checksums(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            assets = package_release.build_release(root, output, "v1.0.0")
            names = {path.name for path in assets}
            with zipfile.ZipFile(output / package_release.BUNDLE_NAME) as archive:
                members = set(archive.namelist())

            self.assertIn("xi-ai-codex-bundle.zip", names)
            self.assertIn("xi-ai-codex-bundle.zip.sha256", names)
            self.assertIn("xi-ai-codex-bootstrap.py", names)
            self.assertIn("xi-ai-codex-bootstrap.py.sha256", names)
            self.assertIn("xi-ai-codex-release.json", names)
            self.assertIn("src/codex_configurator/__main__.py", members)
            self.assertIn("assets/bundled-models.json", members)
            self.assertNotIn("__pycache__", "\n".join(members))

            manifest = json.loads(
                (output / package_release.MANIFEST_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["version"], "v1.0.0")
            self.assertEqual(manifest["bundle"]["name"], package_release.BUNDLE_NAME)
            self.assertEqual(
                manifest["bootstrap"]["name"], package_release.BOOTSTRAP_NAME
            )

    def test_readme_one_line_commands_keep_checksum_verification(self):
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")

        self.assertIn("--version latest --configure", readme)
        self.assertIn("xi-ai-codex-bootstrap.py.sha256", readme)
        self.assertIn("curl.exe", readme)
        self.assertGreaterEqual(readme.count("--progress-bar"), 4)
        self.assertIn("https://api.xi-ai.net/v1/responses", readme)
        self.assertNotIn("| iex", readme.lower())
        self.assertNotIn("curl | sh", readme.lower())


if __name__ == "__main__":
    unittest.main()
