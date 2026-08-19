#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ftplib
import hashlib
import io
import json
import os
import re
import ssl
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


ASSET_NAMES = (
    "xi-ai-codex-bundle.zip",
    "xi-ai-codex-bundle.zip.sha256",
    "xi-ai-codex-bootstrap.py",
    "xi-ai-codex-bootstrap.py.sha256",
    "xi-ai-codex-release.json",
)
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PUBLIC_HOST = "download.xi-ai.net"
PUBLIC_ROOT = "https://download.xi-ai.net/xi-ai-codex"
REMOTE_ROOT = "/xi-ai-codex"
MAX_VERIFY_BYTES = 100 * 1024 * 1024
VERIFY_ATTEMPTS = 5


class PublishError(Exception):
    pass


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PublishError("HTTPS verification does not allow redirects")


_STRICT_OPENER = build_opener(_RejectRedirectHandler())


def _validate_version(version: str) -> str:
    value = version.strip()
    if value == "latest" or TAG_RE.fullmatch(value) is None:
        raise PublishError("release tag is not safe for the publish path")
    return value


def _validate_port(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise PublishError("FTPS port must be an integer between 1 and 65535")
    return value


def _port_from_environment(value: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise PublishError("FTPS port must be an integer between 1 and 65535")
    return _validate_port(int(value))


def _release_assets(dist: Path) -> tuple[Path, ...]:
    root = dist.expanduser().resolve()
    if not root.is_dir():
        raise PublishError("release asset directory does not exist")
    files = tuple(sorted(path.name for path in root.iterdir() if path.is_file()))
    if files != tuple(sorted(ASSET_NAMES)):
        raise PublishError("release directory must contain exactly five fixed assets")
    return tuple(root / name for name in ASSET_NAMES)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download_verified(url: str, *, opener=_STRICT_OPENER.open) -> bytes:
    request = Request(
        url,
        headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
    )
    try:
        with opener(request, timeout=30) as response:
            final_url = response.geturl() if hasattr(response, "geturl") else url
            if final_url != url:
                raise PublishError("HTTPS verification URL changed")
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_VERIFY_BYTES:
                        raise PublishError("HTTPS verification response is too large")
                except ValueError:
                    pass
            data = response.read(MAX_VERIFY_BYTES + 1)
    except PublishError:
        raise
    except HTTPError as exc:
        raise PublishError(f"HTTPS verification failed with HTTP {exc.code}") from exc
    except (URLError, OSError) as exc:
        raise PublishError("HTTPS verification request failed") from exc
    if len(data) > MAX_VERIFY_BYTES:
        raise PublishError("HTTPS verification response is too large")
    return data


def _verify_public_asset(url: str, expected: bytes, *, opener, sleeper=time.sleep) -> None:
    last_error: PublishError | None = None
    for attempt in range(VERIFY_ATTEMPTS):
        try:
            actual = _download_verified(url, opener=opener)
            if len(actual) != len(expected) or _sha256_bytes(actual) != _sha256_bytes(
                expected
            ):
                raise PublishError(
                    "published asset differs from local build: "
                    + url.rsplit("/", 1)[-1]
                )
            return
        except PublishError as exc:
            last_error = exc
            if attempt + 1 < VERIFY_ATTEMPTS:
                sleeper(2)
    assert last_error is not None
    raise last_error


def _directory_exists(ftp: ftplib.FTP_TLS, name: str) -> bool:
    try:
        ftp.cwd(name)
    except ftplib.error_perm as exc:
        if not str(exc).startswith("550"):
            raise PublishError("unable to inspect remote version directory") from exc
        return False
    ftp.cwd(REMOTE_ROOT)
    return True


def _delete_file(ftp: ftplib.FTP_TLS, path: str) -> None:
    try:
        ftp.delete(path)
    except ftplib.all_errors:
        pass


def _remove_staging(
    ftp: ftplib.FTP_TLS,
    staging_name: str,
    assets: tuple[Path, ...],
) -> None:
    for asset in assets:
        _delete_file(ftp, f"{REMOTE_ROOT}/{staging_name}/{asset.name}")
    try:
        ftp.rmd(f"{REMOTE_ROOT}/{staging_name}")
    except ftplib.all_errors:
        pass


def publish_release(
    dist: Path,
    version: str,
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    run_id: str,
    run_attempt: str,
    ftp_factory=ftplib.FTP_TLS,
    opener=_STRICT_OPENER.open,
    output=print,
    sleeper=time.sleep,
) -> None:
    tag = _validate_version(version)
    if host != PUBLIC_HOST:
        raise PublishError(f"FTPS host must be {PUBLIC_HOST}")
    port = _validate_port(port)
    if not username or not password:
        raise PublishError("FTPS credentials are required")
    if not run_id.isdigit() or not run_attempt.isdigit():
        raise PublishError("GitHub run identifiers are invalid")
    assets = _release_assets(dist)
    staging_name = f"_staging-{tag}-{run_id}-{run_attempt}"
    latest_temp = f"latest.json.tmp-{run_id}-{run_attempt}"
    latest_bytes = (
        json.dumps({"schema_version": 1, "version": tag}, indent=2) + "\n"
    ).encode("utf-8")

    ftp = ftp_factory(context=ssl.create_default_context(), timeout=30)
    connected = False
    renamed = False
    try:
        ftp.connect(host, port)
        connected = True
        ftp.login(username, password)
        ftp.prot_p()
        ftp.set_pasv(True)
        ftp.cwd(REMOTE_ROOT)
        if _directory_exists(ftp, tag):
            raise PublishError(f"release version already exists and is immutable: {tag}")

        ftp.mkd(staging_name)
        for asset in assets:
            output(f"Uploading {asset.name}")
            with asset.open("rb") as source:
                ftp.storbinary(f"STOR {staging_name}/{asset.name}", source)

        for asset in assets:
            expected = asset.read_bytes()
            _verify_public_asset(
                f"{PUBLIC_ROOT}/{staging_name}/{asset.name}",
                expected,
                opener=opener,
                sleeper=sleeper,
            )
        ftp.rename(staging_name, tag)
        renamed = True

        for asset in assets:
            expected = asset.read_bytes()
            _verify_public_asset(
                f"{PUBLIC_ROOT}/{tag}/{asset.name}",
                expected,
                opener=opener,
                sleeper=sleeper,
            )

        ftp.storbinary(f"STOR {latest_temp}", io.BytesIO(latest_bytes))
        _verify_public_asset(
            f"{PUBLIC_ROOT}/{latest_temp}",
            latest_bytes,
            opener=opener,
            sleeper=sleeper,
        )
        ftp.rename(latest_temp, "latest.json")
        _verify_public_asset(
            f"{PUBLIC_ROOT}/latest.json",
            latest_bytes,
            opener=opener,
            sleeper=sleeper,
        )
        output(f"Published {tag} and updated latest.json")
    except PublishError:
        raise
    except ftplib.all_errors as exc:
        raise PublishError(f"FTPS publication failed ({exc.__class__.__name__})") from exc
    finally:
        if connected:
            if not renamed:
                _remove_staging(ftp, staging_name, assets)
            _delete_file(ftp, f"{REMOTE_ROOT}/{latest_temp}")
            try:
                ftp.quit()
            except ftplib.all_errors:
                try:
                    ftp.close()
                except OSError:
                    pass
        else:
            try:
                ftp.close()
            except OSError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish Xi-AI Codex over FTPS.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        publish_release(
            args.dist,
            args.version,
            host=os.environ.get("FTPS_HOST", ""),
            port=_port_from_environment(os.environ.get("FTPS_PORT", "")),
            username=os.environ.get("FTPS_USERNAME", ""),
            password=os.environ.get("FTPS_PASSWORD", ""),
            run_id=os.environ.get("GITHUB_RUN_ID", ""),
            run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        )
        return 0
    except PublishError as exc:
        print(f"Publish error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
