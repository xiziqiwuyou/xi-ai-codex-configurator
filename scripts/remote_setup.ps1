$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3

$candidates = @()
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    $candidates += [pscustomobject]@{
        Command = $pyLauncher.Source
        Prefix = @("-3")
    }
}
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
    $candidates += [pscustomobject]@{
        Command = $pythonCommand.Source
        Prefix = @()
    }
}

$selected = $null
foreach ($candidate in $candidates) {
    $command = $candidate.Command
    $prefix = @($candidate.Prefix)
    & $command @prefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
    if ($LASTEXITCODE -eq 0) {
        $selected = $candidate
        break
    }
}
if (-not $selected) {
    Write-Error "Python 3.11 or newer is required."
    exit 1
}

$downloader = @'
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


BASE_URL = "https://download.xi-ai.net/xi-ai-codex"
BOOTSTRAP_NAME = "xi-ai-codex-bootstrap.py"
MANIFEST_NAME = "xi-ai-codex-release.json"
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
CHECKSUM_RE = re.compile(
    r"^([0-9A-Fa-f]{64})[ \t]+\*?xi-ai-codex-bootstrap\.py$"
)
MAX_METADATA_BYTES = 1024 * 1024
MAX_BOOTSTRAP_BYTES = 10 * 1024 * 1024
MAX_BUNDLE_BYTES = 100 * 1024 * 1024
DOWNLOAD_ATTEMPTS = 3


class SetupDownloadError(Exception):
    pass


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(RejectRedirects())


def _download(url: str, destination: Path, limit: int) -> None:
    partial = destination.with_name(destination.name + ".part")
    last_error: Exception | None = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        partial.unlink(missing_ok=True)
        try:
            request = Request(
                url,
                headers={
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "User-Agent": "xi-ai-codex-fixed-setup",
                },
            )
            with OPENER.open(request, timeout=30) as response:
                if response.geturl() != url:
                    raise SetupDownloadError("HTTPS redirects are not allowed")
                declared = response.headers.get("Content-Length")
                expected_size: int | None = None
                if declared is not None:
                    try:
                        expected_size = int(declared)
                    except ValueError as exc:
                        raise SetupDownloadError("invalid Content-Length") from exc
                    if expected_size < 0 or expected_size > limit:
                        raise SetupDownloadError("download exceeds the size limit")
                total = 0
                with partial.open("xb") as output:
                    while True:
                        chunk = response.read(min(1024 * 1024, limit - total + 1))
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > limit:
                            raise SetupDownloadError("download exceeds the size limit")
                        output.write(chunk)
                if expected_size is not None and total != expected_size:
                    raise SetupDownloadError("download length does not match Content-Length")
            partial.replace(destination)
            return
        except HTTPError as exc:
            partial.unlink(missing_ok=True)
            raise SetupDownloadError(f"HTTPS request failed with HTTP {exc.code}") from exc
        except SetupDownloadError:
            partial.unlink(missing_ok=True)
            raise
        except (URLError, OSError) as exc:
            partial.unlink(missing_ok=True)
            last_error = exc
            if attempt + 1 < DOWNLOAD_ATTEMPTS:
                time.sleep(0.5 * (2**attempt))
    raise SetupDownloadError("HTTPS download failed after three attempts") from last_error


def _json_object(path: Path, description: str) -> dict:
    def object_without_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_without_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite number")
            ),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise SetupDownloadError(f"{description} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SetupDownloadError(f"{description} must be a JSON object")
    return value


def _asset(value: object, name: str, size_limit: int) -> tuple[str, int]:
    if not isinstance(value, dict) or set(value) != {"name", "sha256", "size"}:
        raise SetupDownloadError(f"invalid manifest entry for {name}")
    sha256 = value["sha256"]
    size = value["size"]
    if value["name"] != name:
        raise SetupDownloadError(f"invalid manifest asset name for {name}")
    if not isinstance(sha256, str) or HASH_RE.fullmatch(sha256) is None:
        raise SetupDownloadError(f"invalid manifest SHA-256 for {name}")
    if type(size) is not int or size <= 0 or size > size_limit:
        raise SetupDownloadError(f"invalid manifest size for {name}")
    return sha256, size


def main() -> None:
    destination = Path(sys.argv[1]).resolve()
    latest_path = destination / "latest.json"
    _download(f"{BASE_URL}/latest.json", latest_path, MAX_METADATA_BYTES)
    latest = _json_object(latest_path, "latest.json")
    if set(latest) != {"schema_version", "version"}:
        raise SetupDownloadError("latest.json has invalid fields")
    version = latest["version"]
    if (
        type(latest["schema_version"]) is not int
        or latest["schema_version"] != 1
        or not isinstance(version, str)
        or version == "latest"
        or TAG_RE.fullmatch(version) is None
    ):
        raise SetupDownloadError("latest.json has invalid values")

    manifest_path = destination / MANIFEST_NAME
    bootstrap_path = destination / BOOTSTRAP_NAME
    checksum_path = destination / f"{BOOTSTRAP_NAME}.sha256"
    version_url = f"{BASE_URL}/{version}"
    _download(f"{version_url}/{MANIFEST_NAME}", manifest_path, MAX_METADATA_BYTES)
    _download(f"{version_url}/{BOOTSTRAP_NAME}", bootstrap_path, MAX_BOOTSTRAP_BYTES)
    _download(
        f"{version_url}/{BOOTSTRAP_NAME}.sha256",
        checksum_path,
        MAX_METADATA_BYTES,
    )

    manifest = _json_object(manifest_path, "release manifest")
    if set(manifest) != {"schema_version", "version", "bundle", "bootstrap"}:
        raise SetupDownloadError("release manifest has invalid fields")
    if (
        type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or manifest["version"] != version
    ):
        raise SetupDownloadError("release manifest has invalid values")
    _asset(manifest["bundle"], "xi-ai-codex-bundle.zip", MAX_BUNDLE_BYTES)
    expected_hash, expected_size = _asset(
        manifest["bootstrap"], BOOTSTRAP_NAME, MAX_BOOTSTRAP_BYTES
    )

    try:
        checksum_text = checksum_path.read_text(encoding="ascii").strip()
        bootstrap_bytes = bootstrap_path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise SetupDownloadError("unable to read downloaded bootstrap") from exc
    checksum = CHECKSUM_RE.fullmatch(checksum_text)
    actual_hash = hashlib.sha256(bootstrap_bytes).hexdigest()
    if (
        checksum is None
        or checksum.group(1).lower() != expected_hash
        or len(bootstrap_bytes) != expected_size
        or actual_hash != expected_hash
    ):
        raise SetupDownloadError("bootstrap verification failed")
    (destination / "version.txt").write_text(version + "\n", encoding="ascii")


try:
    main()
except SetupDownloadError as exc:
    print(f"Setup download failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
except Exception:
    print("Setup download failed because of an unexpected local error", file=sys.stderr)
    raise SystemExit(1)
'@

$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("xi-ai-codex-setup-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
$exitCode = 1
try {
    $python = $selected.Command
    $pythonArguments = @($selected.Prefix)
    & $python @pythonArguments -c $downloader $temporaryDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Release verification failed."
    }
    $version = (Get-Content -LiteralPath (Join-Path $temporaryDirectory "version.txt") -Raw).Trim()
    $bootstrap = Join-Path $temporaryDirectory "xi-ai-codex-bootstrap.py"
    & $python @pythonArguments $bootstrap --version $version --configure @args
    $exitCode = $LASTEXITCODE
} finally {
    Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
exit $exitCode
