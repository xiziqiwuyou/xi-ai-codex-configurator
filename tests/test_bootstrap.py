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

from scripts import bootstrap, package_release


class FakeResponse:
    def __init__(self, payload: bytes):
        self._stream = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}

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

        with tempfile.TemporaryDirectory() as temp, redirect_stderr(io.StringIO()):
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


if __name__ == "__main__":
    unittest.main()
