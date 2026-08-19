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
    def __init__(
        self,
        payload: bytes,
        *,
        content_length: bool = True,
        final_url: str | None = None,
    ):
        self._stream = io.BytesIO(payload)
        self.headers = (
            {"Content-Length": str(len(payload))} if content_length else {}
        )
        self._final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        return self._stream.read(size)

    def geturl(self):
        return self._final_url


def release_bundle() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("src/codex_configurator/__main__.py", "")
        archive.writestr("assets/bundled-models.json", '{"models": []}')
        archive.writestr("scripts/setup.ps1", "")
        archive.writestr("scripts/setup.sh", "")
    return stream.getvalue()


def release_payloads(
    version: str = "v1.0.0",
    *,
    include_latest: bool = False,
    manifest_changes: dict | None = None,
    bundle_checksum: str | None = None,
    bootstrap_checksum: str | None = None,
) -> tuple[dict[str, bytes], dict]:
    bundle = release_bundle()
    bootstrap_path = Path(bootstrap.__file__).resolve()
    bootstrap_hash = bootstrap._sha256(bootstrap_path)
    manifest = {
        "schema_version": 1,
        "version": version,
        "bundle": {
            "name": bootstrap.BUNDLE_NAME,
            "sha256": hashlib.sha256(bundle).hexdigest(),
            "size": len(bundle),
        },
        "bootstrap": {
            "name": bootstrap.BOOTSTRAP_NAME,
            "sha256": bootstrap_hash,
            "size": bootstrap_path.stat().st_size,
        },
    }
    if manifest_changes:
        for key, value in manifest_changes.items():
            if isinstance(value, dict) and isinstance(manifest.get(key), dict):
                manifest[key].update(value)
            else:
                manifest[key] = value
    urls = {
        name: bootstrap._version_asset_url(version, name)
        for name in bootstrap.VERSION_ASSET_NAMES
    }
    payloads = {
        urls[bootstrap.MANIFEST_NAME]: (json.dumps(manifest) + "\n").encode(),
        urls[bootstrap.BUNDLE_NAME]: bundle,
        urls[bootstrap.CHECKSUM_NAME]: (
            (bundle_checksum or hashlib.sha256(bundle).hexdigest())
            + f"  {bootstrap.BUNDLE_NAME}\n"
        ).encode(),
        urls[bootstrap.BOOTSTRAP_CHECKSUM_NAME]: (
            (bootstrap_checksum or bootstrap_hash)
            + f"  {bootstrap.BOOTSTRAP_NAME}\n"
        ).encode(),
    }
    if include_latest:
        payloads[bootstrap._latest_url()] = json.dumps(
            {"schema_version": 1, "version": version}
        ).encode()
    return payloads, manifest


def mapping_opener(payloads: dict[str, bytes], requests: list[str] | None = None):
    def opener(request, timeout):
        if requests is not None:
            requests.append(request.full_url)
        return FakeResponse(payloads[request.full_url], final_url=request.full_url)

    return opener


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
        bootstrap._read_limited(
            FakeResponse(b"x" * 2048, content_length=False),
            4096,
            progress=events.append,
            stage="未知长度",
        )

        self.assertTrue(all(event.total is None for event in events))
        output = io.StringIO()
        reporter = bootstrap.BootstrapProgress(stream=output, tty=False)
        for event in events:
            reporter(event)
        self.assertIn("已下载", output.getvalue())
        self.assertNotIn("%", output.getvalue())

    def test_retry_progress_resets_the_next_attempt(self):
        events = []
        attempts = []
        url = bootstrap._latest_url()

        def opener(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) == 1:
                raise URLError("temporary reset")
            return FakeResponse(b"ok", final_url=url)

        with patch.object(bootstrap.time, "sleep"):
            bootstrap._open_bytes(
                url,
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
        url = bootstrap._latest_url()

        def opener(request, timeout):
            self.assertEqual(events[-1].state, "start")
            self.assertEqual(events[-1].current, 0)
            return FakeResponse(b"ok", final_url=url)

        bootstrap._open_bytes(
            url,
            opener=opener,
            limit=1024,
            progress=events.append,
            stage="连接下载源",
        )
        self.assertEqual(events[-1].state, "complete")

    def test_tty_progress_uses_in_place_updates_and_finishes_with_newline(self):
        output = io.StringIO()
        reporter = bootstrap.BootstrapProgress(stream=output, tty=True)
        reporter(bootstrap.DownloadProgress("程序包", "update", current=50, total=100))
        reporter(
            bootstrap.DownloadProgress("程序包", "complete", current=100, total=100)
        )
        self.assertIn("\r", output.getvalue())
        self.assertIn("#", output.getvalue())
        self.assertTrue(output.getvalue().endswith("\n"))

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

    def test_source_urls_are_fixed_https_paths(self):
        latest = bootstrap._latest_url()
        manifest = bootstrap._version_asset_url("v1.2.3", bootstrap.MANIFEST_NAME)
        bundle = bootstrap._version_asset_url("v1.2.3", bootstrap.BUNDLE_NAME)
        latest_request = bootstrap._request(latest)
        bundle_request = bootstrap._request(bundle)

        self.assertEqual(
            latest, "https://download.xi-ai.net/xi-ai-codex/latest.json"
        )
        self.assertEqual(
            manifest,
            "https://download.xi-ai.net/xi-ai-codex/v1.2.3/xi-ai-codex-release.json",
        )
        self.assertEqual(latest_request.get_header("Accept"), "application/json")
        self.assertEqual(latest_request.get_header("Cache-control"), "no-cache")
        self.assertEqual(latest_request.get_header("Pragma"), "no-cache")
        self.assertEqual(
            bundle_request.get_header("Accept"),
            "application/octet-stream",
        )
        self.assertIsNone(bundle_request.get_header("Cache-control"))

    def test_untrusted_or_malformed_source_urls_are_rejected(self):
        invalid = [
            "http://download.xi-ai.net/xi-ai-codex/latest.json",
            "https://example.com/xi-ai-codex/latest.json",
            "https://download.xi-ai.net:443/xi-ai-codex/latest.json",
            "https://user@download.xi-ai.net/xi-ai-codex/latest.json",
            "https://download.xi-ai.net/xi-ai-codex/latest.json?x=1",
            "https://download.xi-ai.net/xi-ai-codex/latest.json#x",
            "https://download.xi-ai.net/xi-ai-codex/v1/../latest.json",
            "https://download.xi-ai.net/xi-ai-codex/%2e%2e/latest.json",
            "https://download.xi-ai.net/xi-ai-codex/v1/unknown.zip",
            "https://download.xi-ai.net/xi-ai-codex/latest/xi-ai-codex-release.json",
        ]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(bootstrap.BootstrapError):
                bootstrap._request(url)

    def test_redirect_to_different_release_path_is_rejected(self):
        url = bootstrap._latest_url()
        other = bootstrap._version_asset_url("v1", bootstrap.MANIFEST_NAME)
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap._open_bytes(
                url,
                opener=lambda request, timeout: FakeResponse(
                    b"{}", final_url=other
                ),
                limit=1024,
            )

    def test_default_transport_rejects_redirects_before_following_them(self):
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap._RejectRedirectHandler().redirect_request(
                bootstrap._request(bootstrap._latest_url()),
                None,
                302,
                "Found",
                {"Location": "https://example.com/payload"},
                "https://example.com/payload",
            )

        url = bootstrap._latest_url()
        response = FakeResponse(b"ok", final_url=url)
        with patch.object(
            bootstrap._STRICT_OPENER, "open", return_value=response
        ) as strict_open:
            self.assertEqual(bootstrap._open_bytes(url, limit=1024), b"ok")
        strict_open.assert_called_once()

    def test_latest_resolves_pointer_then_matching_manifest(self):
        payloads, _ = release_payloads(include_latest=True)
        requests = []

        manifest = bootstrap.resolve_release(
            "latest", opener=mapping_opener(payloads, requests)
        )

        self.assertEqual(manifest.version, "v1.0.0")
        self.assertEqual(
            requests,
            [
                bootstrap._latest_url(),
                bootstrap._version_asset_url("v1.0.0", bootstrap.MANIFEST_NAME),
            ],
        )

    def test_explicit_version_skips_latest_pointer(self):
        payloads, _ = release_payloads()
        requests = []
        manifest = bootstrap.resolve_release(
            "v1.0.0", opener=mapping_opener(payloads, requests)
        )
        self.assertEqual(manifest.version, "v1.0.0")
        self.assertNotIn(bootstrap._latest_url(), requests)

    def test_latest_pointer_rejects_unknown_fields_and_unsafe_version(self):
        for pointer in (
            {"schema_version": 1, "version": "v1", "url": "https://example.com"},
            {"schema_version": 1, "version": "../v1"},
            {"schema_version": True, "version": "v1"},
            {"schema_version": 1, "version": "latest"},
        ):
            with self.subTest(pointer=pointer):
                payloads, _ = release_payloads()
                payloads[bootstrap._latest_url()] = json.dumps(pointer).encode()
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap.resolve_release(
                        "latest", opener=mapping_opener(payloads)
                    )

    def test_manifest_rejects_schema_version_name_size_and_hash_mismatches(self):
        invalid_changes = [
            {"schema_version": 2},
            {"version": "v2.0.0"},
            {"bundle": {"name": "other.zip"}},
            {"bundle": {"size": True}},
            {"bundle": {"size": bootstrap.MAX_DOWNLOAD_BYTES + 1}},
            {"bundle": {"sha256": "A" * 64}},
            {"bootstrap": {"name": "setup.py"}},
            {"bootstrap": {"sha256": "0" * 63}},
            {"unexpected": "field"},
        ]
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                payloads, _ = release_payloads(manifest_changes=changes)
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap.resolve_release(
                        "v1.0.0", opener=mapping_opener(payloads)
                    )

    def test_manifest_rejects_duplicate_json_keys(self):
        url = bootstrap._version_asset_url("v1", bootstrap.MANIFEST_NAME)
        payload = (
            b'{"schema_version":1,"schema_version":1,"version":"v1",'
            b'"bundle":{},"bootstrap":{}}'
        )
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.resolve_release(
                "v1", opener=mapping_opener({url: payload})
            )

    def test_validated_cache_skips_bundle_and_bundle_checksum_downloads(self):
        payloads, _ = release_payloads()
        requests = []
        with tempfile.TemporaryDirectory() as temp:
            bootstrap.install_release(
                "v1.0.0", Path(temp), opener=mapping_opener(payloads)
            )
            requests.clear()
            events = []
            bootstrap.install_release(
                "v1.0.0",
                Path(temp),
                opener=mapping_opener(payloads, requests),
                progress=events.append,
            )

        self.assertNotIn(
            bootstrap._version_asset_url("v1.0.0", bootstrap.BUNDLE_NAME), requests
        )
        self.assertNotIn(
            bootstrap._version_asset_url("v1.0.0", bootstrap.CHECKSUM_NAME),
            requests,
        )
        self.assertIn(
            bootstrap._version_asset_url(
                "v1.0.0", bootstrap.BOOTSTRAP_CHECKSUM_NAME
            ),
            requests,
        )
        self.assertTrue(
            any(
                event.stage == "已验证缓存，跳过程序包下载"
                and event.state == "complete"
                for event in events
            )
        )

    def test_unsupported_python_stops_before_https_request(self):
        with redirect_stderr(io.StringIO()):
            with patch.object(bootstrap.sys, "version_info", (3, 10, 9)):
                result = bootstrap.main(
                    [],
                    opener=lambda request, timeout: (_ for _ in ()).throw(
                        AssertionError("HTTPS source must not be called")
                    ),
                )
        self.assertEqual(result, 1)

    def test_transient_https_failures_are_retried(self):
        attempts = []
        url = bootstrap._latest_url()

        def opener(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) < 3:
                raise URLError("temporary reset")
            return FakeResponse(b"ok", final_url=url)

        with patch.object(bootstrap.time, "sleep") as sleeper:
            payload = bootstrap._open_bytes(url, opener=opener, limit=1024)
        self.assertEqual(payload, b"ok")
        self.assertEqual(len(attempts), 3)
        self.assertEqual([call.args[0] for call in sleeper.call_args_list], [0.5, 1.0])

    def test_verified_latest_defaults_to_detect_only(self):
        payloads, manifest = release_payloads(include_latest=True)
        commands = []

        def runner(command, **kwargs):
            commands.append((command, kwargs))
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            result = bootstrap.main(
                ["--cache-dir", temp],
                opener=mapping_opener(payloads),
                runner=runner,
            )
            cache = Path(temp) / "v1.0.0"
            marker = (cache / ".release-sha256").read_text().strip()
            cache_exists = (cache / "src/codex_configurator/__main__.py").is_file()

        self.assertEqual(result, 0)
        self.assertEqual(commands[0][0][-2:], ["setup", "--detect-only"])
        self.assertTrue(cache_exists)
        self.assertEqual(marker, manifest["bundle"]["sha256"])

    def test_configure_flag_does_not_force_detect_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in bootstrap.REQUIRED_PATHS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            commands = []
            with patch.object(
                bootstrap, "install_release", return_value=("v1.0.0", root)
            ), redirect_stdout(io.StringIO()):
                result = bootstrap.main(
                    ["--version", "v1.0.0", "--configure"],
                    runner=lambda command, **kwargs: commands.append(command)
                    or SimpleNamespace(returncode=0),
                )

        self.assertEqual(result, 0)
        self.assertEqual(commands[0][-1], "setup")
        self.assertNotIn("--detect-only", commands[0])

    def test_bundle_checksum_mismatch_stops_before_execution(self):
        payloads, _ = release_payloads(bundle_checksum="0" * 64)
        called = False

        def runner(command, **kwargs):
            nonlocal called
            called = True
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as temp, redirect_stderr(
            io.StringIO()
        ), redirect_stdout(io.StringIO()):
            result = bootstrap.main(
                ["--version", "v1.0.0", "--cache-dir", temp],
                opener=mapping_opener(payloads),
                runner=runner,
            )
        self.assertEqual(result, 1)
        self.assertFalse(called)

    def test_manifest_bundle_size_or_hash_mismatch_stops_before_execution(self):
        cases = [
            {"bundle": {"size": len(release_bundle()) + 1}},
            {"bundle": {"sha256": "0" * 64}},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                payloads, _ = release_payloads(manifest_changes=changes)
                called = False

                def runner(command, **kwargs):
                    nonlocal called
                    called = True
                    return SimpleNamespace(returncode=0)

                with tempfile.TemporaryDirectory() as temp, redirect_stderr(
                    io.StringIO()
                ), redirect_stdout(io.StringIO()):
                    result = bootstrap.main(
                        ["--version", "v1.0.0", "--cache-dir", temp],
                        opener=mapping_opener(payloads),
                        runner=runner,
                    )
                self.assertEqual(result, 1)
                self.assertFalse(called)

    def test_bootstrap_checksum_or_manifest_mismatch_stops_before_bundle_download(self):
        cases = [
            ({}, "0" * 64),
            ({"bootstrap": {"size": Path(bootstrap.__file__).stat().st_size + 1}}, None),
        ]
        for changes, checksum in cases:
            with self.subTest(changes=changes, checksum=checksum):
                payloads, _ = release_payloads(
                    manifest_changes=changes, bootstrap_checksum=checksum
                )
                requests = []
                with tempfile.TemporaryDirectory() as temp, self.assertRaises(
                    bootstrap.BootstrapError
                ):
                    bootstrap.install_release(
                        "v1.0.0",
                        Path(temp),
                        opener=mapping_opener(payloads, requests),
                    )
                self.assertNotIn(
                    bootstrap._version_asset_url("v1.0.0", bootstrap.BUNDLE_NAME),
                    requests,
                )

    def test_short_content_length_response_is_rejected(self):
        response = FakeResponse(b"short")
        response.headers["Content-Length"] = "10"
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap._read_limited(response, 1024)

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

    def test_checksum_must_name_the_expected_asset(self):
        with tempfile.TemporaryDirectory() as temp:
            checksum = Path(temp) / bootstrap.CHECKSUM_NAME
            checksum.write_text(f"{'0' * 64}  another-file.zip\n", encoding="ascii")
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap._parse_checksum(checksum)

    def test_version_defaults_to_latest_and_repo_argument_is_removed(self):
        args = bootstrap.build_parser().parse_args([])
        self.assertEqual(args.version, "latest")
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            bootstrap.build_parser().parse_args(["--repo", "owner/repo"])

        with redirect_stderr(io.StringIO()):
            result = bootstrap.main(
                ["--repo", "owner/repo"],
                opener=lambda request, timeout: (_ for _ in ()).throw(
                    AssertionError("removed --repo must fail before network")
                ),
            )
        self.assertEqual(result, 1)

    def test_unsafe_version_stops_before_network(self):
        with redirect_stderr(io.StringIO()):
            result = bootstrap.main(
                ["--version", "../v1"],
                opener=lambda request, timeout: (_ for _ in ()).throw(
                    AssertionError("network must not be called")
                ),
            )
        self.assertEqual(result, 1)


class PackageReleaseTests(unittest.TestCase):
    def test_release_package_contains_runtime_and_five_fixed_assets(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            assets = package_release.build_release(root, output, "v1.0.0")
            names = {path.name for path in assets}
            with zipfile.ZipFile(output / package_release.BUNDLE_NAME) as archive:
                members = set(archive.namelist())

            self.assertEqual(
                names,
                {
                    "xi-ai-codex-bundle.zip",
                    "xi-ai-codex-bundle.zip.sha256",
                    "xi-ai-codex-bootstrap.py",
                    "xi-ai-codex-bootstrap.py.sha256",
                    "xi-ai-codex-release.json",
                },
            )
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

    def test_readme_one_line_commands_use_only_verified_https_source(self):
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        source = "https://download.xi-ai.net/xi-ai-codex"

        self.assertGreaterEqual(readme.count(source), 7)
        self.assertIn("latest.json", readme)
        self.assertIn("xi-ai-codex-release.json", readme)
        self.assertIn("xi-ai-codex-bootstrap.py.sha256", readme)
        self.assertIn("--version $v --configure", readme)
        self.assertIn('--version "$v" --configure', readme)
        self.assertIn("curl.exe", readme)
        self.assertIn("--progress-bar", readme)
        self.assertIn("https://api.xi-ai.net/v1/responses", readme)
        self.assertNotIn("api.github.com", readme)
        self.assertNotIn("github.com/OWNER/REPO/releases", readme)
        self.assertNotIn("FTPS_PASSWORD", readme)
        self.assertNotIn("| iex", readme.lower())
        self.assertNotIn("curl | sh", readme.lower())

    def test_release_workflow_stages_versions_and_replaces_latest_last(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("FTPS_HOST: ${{ secrets.FTPS_HOST }}", workflow)
        self.assertIn("FTPS_PORT: ${{ secrets.FTPS_PORT }}", workflow)
        self.assertIn("FTPS_USERNAME: ${{ secrets.FTPS_USERNAME }}", workflow)
        self.assertIn("FTPS_PASSWORD: ${{ secrets.FTPS_PASSWORD }}", workflow)
        self.assertIn("LFTP_PASSWORD=\"$FTPS_PASSWORD\"", workflow)
        self.assertIn("--env-password", workflow)
        self.assertIn("--user \"$FTPS_USERNAME\"", workflow)
        self.assertIn("staging_version=", workflow)
        self.assertIn("cls -d $remote_version", workflow)
        self.assertIn("^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", workflow)
        self.assertIn("set ftp:ssl-auth TLS", workflow)
        self.assertIn("set ftp:ssl-protect-list true", workflow)
        self.assertIn("set ssl:check-hostname yes", workflow)
        self.assertIn("-H 'Cache-Control: no-cache'", workflow)
        self.assertIn("$staging_name/$asset", workflow)
        self.assertIn("verify/final-$asset", workflow)
        self.assertIn("mv $staging_version $remote_version", workflow)
        self.assertIn("mv $remote_root/$latest_temp $remote_root/latest.json", workflow)
        self.assertNotIn("gh release", workflow)
        self.assertNotIn("rm $remote_root/latest.json", workflow)
        self.assertLess(
            workflow.index("$staging_name/$asset"),
            workflow.index("mv $staging_version $remote_version"),
        )
        self.assertLess(
            workflow.index("mv $staging_version $remote_version"),
            workflow.index("put pointer/latest.json"),
        )


if __name__ == "__main__":
    unittest.main()
