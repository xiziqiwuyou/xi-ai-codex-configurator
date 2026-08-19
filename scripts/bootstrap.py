#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


BUNDLE_NAME = "xi-ai-codex-bundle.zip"
CHECKSUM_NAME = f"{BUNDLE_NAME}.sha256"
BOOTSTRAP_NAME = "xi-ai-codex-bootstrap.py"
BOOTSTRAP_CHECKSUM_NAME = f"{BOOTSTRAP_NAME}.sha256"
MANIFEST_NAME = "xi-ai-codex-release.json"
LATEST_NAME = "latest.json"
DOWNLOAD_HOST = "download.xi-ai.net"
DOWNLOAD_BASE_PATH = "/xi-ai-codex"
DOWNLOAD_BASE_URL = f"https://{DOWNLOAD_HOST}{DOWNLOAD_BASE_PATH}"
MAX_METADATA_BYTES = 1024 * 1024
MAX_BOOTSTRAP_BYTES = 10 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_EXTRACT_BYTES = 300 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
DOWNLOAD_ATTEMPTS = 3
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
CHECKSUM_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
VERSION_ASSET_NAMES = frozenset(
    {
        BUNDLE_NAME,
        CHECKSUM_NAME,
        BOOTSTRAP_NAME,
        BOOTSTRAP_CHECKSUM_NAME,
        MANIFEST_NAME,
    }
)
REQUIRED_PATHS = (
    Path("src/codex_configurator/__main__.py"),
    Path("assets/bundled-models.json"),
    Path("scripts/setup.ps1"),
    Path("scripts/setup.sh"),
)


class BootstrapError(Exception):
    pass


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BootstrapError("下载源不允许 HTTP 重定向")


_STRICT_OPENER = build_opener(_RejectRedirectHandler())


def _strict_urlopen(request: Request, timeout: int):
    return _STRICT_OPENER.open(request, timeout=timeout)


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    bundle: ReleaseAsset
    bootstrap: ReleaseAsset


DownloadState = Literal["start", "update", "complete", "retry"]


@dataclass(frozen=True)
class DownloadProgress:
    stage: str
    state: DownloadState
    current: int | None = None
    total: int | None = None
    attempt: int = 1

    @property
    def bytes_downloaded(self) -> int | None:
        return self.current

    @property
    def total_bytes(self) -> int | None:
        return self.total

    @property
    def status(self) -> DownloadState:
        return self.state


DownloadProgressCallback = Callable[[DownloadProgress], None]


class BootstrapProgress:
    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        tty: bool | None = None,
        percent_step: int = 10,
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.tty = self.stream.isatty() if tty is None else tty
        self.percent_step = max(1, min(percent_step, 100))
        self._last_bucket: dict[str, int] = {}
        self._last_unknown: dict[str, int] = {}
        self._line_width = 0

    @staticmethod
    def _format_bytes(value: int) -> str:
        if value < 1024 * 1024:
            return f"{value / 1024:.1f} KiB"
        return f"{value / (1024 * 1024):.1f} MiB"

    def _should_emit(self, event: DownloadProgress) -> bool:
        if event.state != "update":
            return True
        if event.current is None:
            return False
        if event.total is not None and event.total > 0:
            bucket = int(event.current * 100 / event.total) // self.percent_step
            if self._last_bucket.get(event.stage) == bucket:
                return False
            self._last_bucket[event.stage] = bucket
            return True
        previous = self._last_unknown.get(event.stage, -1_048_576)
        if event.current - previous < 1_048_576:
            return False
        self._last_unknown[event.stage] = event.current
        return True

    def __call__(self, event: DownloadProgress) -> None:
        if event.state in {"start", "retry"}:
            self._last_bucket.pop(event.stage, None)
            self._last_unknown.pop(event.stage, None)
        if not self.tty and not self._should_emit(event):
            return
        if event.state == "start":
            rendered = f"[{event.stage}] 开始（第 {event.attempt} 次）"
        elif event.state == "retry":
            rendered = f"[{event.stage}] 网络波动，准备第 {event.attempt} 次重试"
        elif event.state == "complete":
            rendered = f"[{event.stage}] 完成"
            if event.current is not None:
                rendered += f"（{self._format_bytes(event.current)}）"
        elif event.total is not None and event.total > 0:
            assert event.current is not None
            percent = min(100, int(event.current * 100 / event.total))
            filled = min(20, int(percent * 20 / 100))
            bar = "#" * filled + "-" * (20 - filled)
            rendered = (
                f"[{event.stage}] [{bar}] {percent:3d}% "
                f"{self._format_bytes(event.current)}/{self._format_bytes(event.total)}"
            )
        else:
            assert event.current is not None
            rendered = f"[{event.stage}] 已下载 {self._format_bytes(event.current)}"

        if self.tty:
            padding = " " * max(0, self._line_width - len(rendered))
            self.stream.write(f"\r{rendered}{padding}")
            self.stream.flush()
            self._line_width = len(rendered)
            if event.state == "complete":
                self.stream.write("\n")
                self.stream.flush()
                self._line_width = 0
        else:
            self.stream.write(rendered + "\n")
            self.stream.flush()
        if event.state == "complete":
            self._last_bucket.pop(event.stage, None)
            self._last_unknown.pop(event.stage, None)


def _emit_progress(
    callback: DownloadProgressCallback | None,
    event: DownloadProgress,
) -> None:
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        # Download progress is observational and cannot change verification behavior.
        pass


def _require_supported_python(version_info=None) -> None:
    current = sys.version_info if version_info is None else version_info
    if tuple(current[:2]) < (3, 11):
        raise BootstrapError("需要 Python 3.11 或更高版本")


def _validate_source_url(url: str) -> str:
    if not isinstance(url, str):
        raise BootstrapError("下载 URL 无效")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != DOWNLOAD_HOST
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise BootstrapError("下载 URL 不在受信任的 HTTPS 源内")
    if parsed.path == f"{DOWNLOAD_BASE_PATH}/{LATEST_NAME}":
        return url
    prefix = f"{DOWNLOAD_BASE_PATH}/"
    if not parsed.path.startswith(prefix):
        raise BootstrapError("下载 URL 路径无效")
    parts = parsed.path[len(prefix) :].split("/")
    if (
        len(parts) != 2
        or parts[0] == "latest"
        or TAG_RE.fullmatch(parts[0]) is None
        or parts[1] not in VERSION_ASSET_NAMES
    ):
        raise BootstrapError("下载 URL 路径无效")
    return url


def _latest_url() -> str:
    return _validate_source_url(f"{DOWNLOAD_BASE_URL}/{LATEST_NAME}")


def _version_asset_url(version: str, asset_name: str) -> str:
    if (
        version == "latest"
        or TAG_RE.fullmatch(version) is None
        or asset_name not in VERSION_ASSET_NAMES
    ):
        raise BootstrapError("版本或发布资产名称无效")
    return _validate_source_url(f"{DOWNLOAD_BASE_URL}/{version}/{asset_name}")


def _request(url: str) -> Request:
    trusted_url = _validate_source_url(url)
    is_latest = trusted_url == f"{DOWNLOAD_BASE_URL}/{LATEST_NAME}"
    accept = (
        "application/json"
        if is_latest or trusted_url.endswith(f"/{MANIFEST_NAME}")
        else "application/octet-stream"
    )
    headers = {
        "Accept": accept,
        "User-Agent": "xi-ai-codex-bootstrap",
    }
    if is_latest:
        headers.update({"Cache-Control": "no-cache", "Pragma": "no-cache"})
    return Request(
        trusted_url,
        headers=headers,
    )


def _read_limited(
    response,
    limit: int,
    *,
    progress: DownloadProgressCallback | None = None,
    stage: str = "下载",
    attempt: int = 1,
    announce_start: bool = True,
) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise BootstrapError("下载响应超过大小限制")
        except ValueError:
            pass
    total_size: int | None = None
    if content_length:
        try:
            parsed_length = int(content_length)
            if parsed_length >= 0:
                total_size = parsed_length
        except ValueError:
            pass
    if announce_start:
        _emit_progress(
            progress,
            DownloadProgress(
                stage, "start", current=0, total=total_size, attempt=attempt
            ),
        )
    elif total_size is not None:
        _emit_progress(
            progress,
            DownloadProgress(
                stage, "update", current=0, total=total_size, attempt=attempt
            ),
        )
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise BootstrapError("下载响应超过大小限制")
        chunks.append(chunk)
        _emit_progress(
            progress,
            DownloadProgress(
                stage,
                "update",
                current=total,
                total=total_size,
                attempt=attempt,
            ),
        )
    if total_size is not None and total != total_size:
        raise BootstrapError("下载响应长度与 Content-Length 不一致")
    _emit_progress(
        progress,
        DownloadProgress(
            stage,
            "complete",
            current=total,
            total=total_size,
            attempt=attempt,
        ),
    )
    return b"".join(chunks)


def _open_bytes(
    url: str,
    *,
    opener=_strict_urlopen,
    limit: int,
    progress: DownloadProgressCallback | None = None,
    stage: str = "下载",
) -> bytes:
    last_error: URLError | OSError | None = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            _emit_progress(
                progress,
                DownloadProgress(
                    stage,
                    "start",
                    current=0,
                    attempt=attempt + 1,
                ),
            )
            with opener(_request(url), timeout=30) as response:
                final_url = response.geturl() if hasattr(response, "geturl") else url
                _validate_source_url(final_url)
                if final_url != url:
                    raise BootstrapError("下载源重定向到了不匹配的发布路径")
                return _read_limited(
                    response,
                    limit,
                    progress=progress,
                    stage=stage,
                    attempt=attempt + 1,
                    announce_start=False,
                )
        except BootstrapError:
            raise
        except HTTPError as exc:
            raise BootstrapError(f"HTTPS 下载请求失败，HTTP {exc.code}") from exc
        except (URLError, OSError) as exc:
            last_error = exc
            if attempt + 1 < DOWNLOAD_ATTEMPTS:
                _emit_progress(
                    progress,
                    DownloadProgress(
                        stage,
                        "retry",
                        current=0,
                        attempt=attempt + 2,
                    ),
                )
                time.sleep(0.5 * (2**attempt))
    raise BootstrapError("多次重试后仍无法连接 HTTPS 下载源") from last_error


def _validate_version(version: str) -> str:
    if not isinstance(version, str):
        raise BootstrapError("发布版本无效")
    value = version.strip()
    if value != "latest" and not TAG_RE.fullmatch(value):
        raise BootstrapError("发布版本包含不支持的字符")
    return value


def _parse_json_object(payload: bytes, description: str) -> dict:
    def object_without_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("non-finite number")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=object_without_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BootstrapError(f"{description}不是有效的 JSON") from exc
    if not isinstance(value, dict):
        raise BootstrapError(f"{description}必须是 JSON 对象")
    return value


def _parse_latest(payload: bytes) -> str:
    pointer = _parse_json_object(payload, "latest.json")
    if set(pointer) != {"schema_version", "version"}:
        raise BootstrapError("latest.json 字段无效")
    if type(pointer["schema_version"]) is not int or pointer["schema_version"] != 1:
        raise BootstrapError("latest.json schema_version 无效")
    version = pointer["version"]
    if (
        not isinstance(version, str)
        or version == "latest"
        or TAG_RE.fullmatch(version) is None
    ):
        raise BootstrapError("latest.json 版本无效")
    return version


def _parse_manifest_asset(
    value: object,
    *,
    expected_name: str,
    size_limit: int,
) -> ReleaseAsset:
    if not isinstance(value, dict) or set(value) != {"name", "sha256", "size"}:
        raise BootstrapError(f"发布清单中的 {expected_name} 描述无效")
    name = value["name"]
    sha256 = value["sha256"]
    size = value["size"]
    if name != expected_name:
        raise BootstrapError(f"发布清单中的资产名称不匹配：{expected_name}")
    if not isinstance(sha256, str) or HASH_RE.fullmatch(sha256) is None:
        raise BootstrapError(f"发布清单中的 SHA-256 无效：{expected_name}")
    if type(size) is not int or size <= 0 or size > size_limit:
        raise BootstrapError(f"发布清单中的资产大小无效：{expected_name}")
    return ReleaseAsset(name=name, sha256=sha256, size=size)


def _parse_manifest(payload: bytes, expected_version: str) -> ReleaseManifest:
    manifest = _parse_json_object(payload, "发布清单")
    if set(manifest) != {"schema_version", "version", "bundle", "bootstrap"}:
        raise BootstrapError("发布清单字段无效")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise BootstrapError("发布清单 schema_version 无效")
    version = manifest["version"]
    if version != expected_version:
        raise BootstrapError("发布清单版本与请求版本不一致")
    return ReleaseManifest(
        version=expected_version,
        bundle=_parse_manifest_asset(
            manifest["bundle"],
            expected_name=BUNDLE_NAME,
            size_limit=MAX_DOWNLOAD_BYTES,
        ),
        bootstrap=_parse_manifest_asset(
            manifest["bootstrap"],
            expected_name=BOOTSTRAP_NAME,
            size_limit=MAX_BOOTSTRAP_BYTES,
        ),
    )


def resolve_release(
    version: str,
    *,
    opener=_strict_urlopen,
    progress: DownloadProgressCallback | None = None,
) -> ReleaseManifest:
    requested = _validate_version(version)
    if requested == "latest":
        pointer = _open_bytes(
            _latest_url(),
            opener=opener,
            limit=MAX_METADATA_BYTES,
            progress=progress,
            stage="下载 latest.json",
        )
        requested = _parse_latest(pointer)
    payload = _open_bytes(
        _version_asset_url(requested, MANIFEST_NAME),
        opener=opener,
        limit=MAX_METADATA_BYTES,
        progress=progress,
        stage="下载发布清单",
    )
    return _parse_manifest(payload, requested)


def _download(
    url: str,
    destination: Path,
    *,
    opener=_strict_urlopen,
    limit: int,
    expected_size: int | None = None,
    progress: DownloadProgressCallback | None = None,
    stage: str = "下载",
) -> None:
    content = _open_bytes(
        url,
        opener=opener,
        limit=limit,
        progress=progress,
        stage=stage,
    )
    if expected_size is not None and len(content) != expected_size:
        raise BootstrapError("下载的发布资产大小与清单不一致")
    destination.write_bytes(content)


def _parse_checksum(path: Path, *, expected_name: str = BUNDLE_NAME) -> str:
    try:
        lines = [
            line.strip()
            for line in path.read_text(encoding="ascii").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError) as exc:
        raise BootstrapError("Release 校验文件无效") from exc
    if len(lines) != 1:
        raise BootstrapError("Release 校验文件无效")
    fields = lines[0].split(maxsplit=1)
    if len(fields) != 2 or not CHECKSUM_HASH_RE.fullmatch(fields[0]):
        raise BootstrapError("Release 校验文件无效")
    filename = fields[1].lstrip("*")
    if filename != expected_name:
        raise BootstrapError("Release 校验文件指向了错误的程序包")
    return fields[0].lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_checksum(path: Path, expected: str) -> None:
    if not hmac.compare_digest(_sha256(path), expected):
        raise BootstrapError("下载的 Release 程序包未通过 SHA-256 校验")


def _verify_release_file(path: Path, asset: ReleaseAsset, description: str) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise BootstrapError(f"无法读取{description}") from exc
    if size != asset.size:
        raise BootstrapError(f"{description}大小与发布清单不一致")
    if not hmac.compare_digest(_sha256(path), asset.sha256):
        raise BootstrapError(f"{description}未通过发布清单 SHA-256 校验")


def _safe_zip_member(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    member = PurePosixPath(normalized)
    if (
        not normalized
        or member.is_absolute()
        or any(part in {"", "."} for part in member.parts)
        or ".." in member.parts
        or any(":" in part for part in member.parts)
    ):
        raise BootstrapError("Release 程序包包含不安全路径")
    return member


def safe_extract(bundle: Path, destination: Path) -> None:
    try:
        archive = zipfile.ZipFile(bundle)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BootstrapError("Release 程序包不是有效的 ZIP 文件") from exc
    with archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise BootstrapError("Release 程序包包含过多文件")
        total = sum(info.file_size for info in members)
        if total > MAX_EXTRACT_BYTES:
            raise BootstrapError("Release 程序包超过解压大小限制")
        root = destination.resolve()
        seen: set[str] = set()
        for info in members:
            member = _safe_zip_member(info.filename)
            member_key = member.as_posix().casefold()
            if member_key in seen:
                raise BootstrapError("Release 程序包包含重复路径")
            seen.add(member_key)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise BootstrapError("Release 程序包包含符号链接")
            target = destination.joinpath(*member.parts)
            try:
                target.resolve().relative_to(root)
            except ValueError as exc:
                raise BootstrapError("Release 程序包包含不安全路径") from exc
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def _validate_bundle_root(path: Path) -> None:
    missing = [str(item) for item in REQUIRED_PATHS if not (path / item).is_file()]
    if missing:
        raise BootstrapError(
            "Release 程序包不完整，缺少：" + ", ".join(missing)
        )


def _default_cache_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return base / "XiAiCodexConfigurator/versions"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "xi-ai-codex/versions"


def _cache_target(cache_root: Path, tag: str) -> Path:
    target = (cache_root / tag).resolve()
    try:
        target.relative_to(cache_root.resolve())
    except ValueError as exc:
        raise BootstrapError("Release 缓存路径无效") from exc
    return target


def _cached_bundle_is_valid(target: Path, expected_hash: str) -> bool:
    marker = target / ".release-sha256"
    try:
        if marker.read_text(encoding="ascii").strip() != expected_hash:
            return False
        _validate_bundle_root(target)
        return True
    except (OSError, UnicodeDecodeError, BootstrapError):
        return False


def install_release(
    version: str,
    cache_root: Path,
    *,
    opener=_strict_urlopen,
    refresh: bool = False,
    progress: DownloadProgressCallback | None = None,
    bootstrap_path: Path | None = None,
) -> tuple[str, Path]:
    manifest = resolve_release(version, opener=opener, progress=progress)
    tag = manifest.version
    cache_root = cache_root.expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    target = _cache_target(cache_root, tag)

    with tempfile.TemporaryDirectory(prefix="xi-ai-codex-download-") as temp:
        temporary = Path(temp)
        bootstrap_checksum_path = temporary / BOOTSTRAP_CHECKSUM_NAME
        _download(
            _version_asset_url(tag, BOOTSTRAP_CHECKSUM_NAME),
            bootstrap_checksum_path,
            opener=opener,
            limit=1024 * 1024,
            progress=progress,
            stage="下载 Bootstrap 校验文件",
        )
        bootstrap_checksum = _parse_checksum(
            bootstrap_checksum_path, expected_name=BOOTSTRAP_NAME
        )
        if not hmac.compare_digest(bootstrap_checksum, manifest.bootstrap.sha256):
            raise BootstrapError("Bootstrap 校验文件与发布清单不一致")
        local_bootstrap = (
            Path(__file__).resolve()
            if bootstrap_path is None
            else bootstrap_path.expanduser().resolve()
        )
        _verify_release_file(local_bootstrap, manifest.bootstrap, "本地 Bootstrap")

        if not refresh and target.is_dir() and _cached_bundle_is_valid(
            target, manifest.bundle.sha256
        ):
            _emit_progress(
                progress,
                DownloadProgress("已验证缓存，跳过程序包下载", "complete"),
            )
            return tag, target

        checksum_path = temporary / CHECKSUM_NAME
        _download(
            _version_asset_url(tag, CHECKSUM_NAME),
            checksum_path,
            opener=opener,
            limit=1024 * 1024,
            progress=progress,
            stage="下载 Release 校验文件",
        )
        expected_hash = _parse_checksum(checksum_path)
        if not hmac.compare_digest(expected_hash, manifest.bundle.sha256):
            raise BootstrapError("程序包校验文件与发布清单不一致")

        bundle_path = temporary / BUNDLE_NAME
        _download(
            _version_asset_url(tag, BUNDLE_NAME),
            bundle_path,
            opener=opener,
            limit=MAX_DOWNLOAD_BYTES,
            expected_size=manifest.bundle.size,
            progress=progress,
            stage="下载 Release 程序包",
        )
        _emit_progress(progress, DownloadProgress("校验 SHA-256", "start"))
        _verify_release_file(bundle_path, manifest.bundle, "下载的 Release 程序包")
        _verify_checksum(bundle_path, expected_hash)
        _emit_progress(progress, DownloadProgress("校验 SHA-256", "complete"))

        stage = Path(tempfile.mkdtemp(prefix=f".{tag}-", dir=cache_root))
        try:
            _emit_progress(progress, DownloadProgress("解压程序包", "start"))
            safe_extract(bundle_path, stage)
            _validate_bundle_root(stage)
            _emit_progress(progress, DownloadProgress("解压程序包", "complete"))
            (stage / ".release-sha256").write_text(
                expected_hash + "\n", encoding="ascii"
            )
            _emit_progress(progress, DownloadProgress("安装本地缓存", "start"))
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            os.replace(stage, target)
            _emit_progress(progress, DownloadProgress("安装本地缓存", "complete"))
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
    return tag, target


def run_setup(bundle_root: Path, setup_args: list[str], *, runner=subprocess.run) -> int:
    environment = os.environ.copy()
    source_path = str(bundle_root / "src")
    current_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path + os.pathsep + current_pythonpath
        if current_pythonpath
        else source_path
    )
    result = runner(
        [sys.executable, "-m", "codex_configurator", "setup", *setup_args],
        cwd=bundle_root,
        env=environment,
        check=False,
    )
    return int(result.returncode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从固定 HTTPS 源下载、校验并运行 Xi-AI Codex。"
    )
    parser.add_argument(
        "--version",
        default=os.environ.get("XI_AI_CODEX_VERSION", "latest"),
        help="指定发布版本；默认通过 latest.json 解析最新版",
    )
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_root())
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--configure",
        action="store_true",
        help="运行交互式配置；不传此参数时只执行安全探测",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    opener=_strict_urlopen,
    runner=subprocess.run,
    progress: DownloadProgressCallback | None = None,
) -> int:
    parser = build_parser()
    args, setup_args = parser.parse_known_args(argv)
    if setup_args and setup_args[0] == "--":
        setup_args = setup_args[1:]
    try:
        _require_supported_python()
        if any(arg == "--repo" or arg.startswith("--repo=") for arg in setup_args):
            raise BootstrapError("--repo 已移除；发布源固定为 download.xi-ai.net")
        version = _validate_version(args.version)
        if not args.configure and "--detect-only" not in setup_args:
            setup_args.insert(0, "--detect-only")
        progress_callback = progress if progress is not None else BootstrapProgress()
        tag, bundle_root = install_release(
            version,
            args.cache_dir,
            opener=opener,
            refresh=args.refresh,
            progress=progress_callback,
        )
        print(f"HTTPS 发布源校验通过：{DOWNLOAD_BASE_URL}/{tag}")
        print(f"本地程序包：{bundle_root}")
        return run_setup(bundle_root, setup_args, runner=runner)
    except BootstrapError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"错误：本地发布操作失败（{exc.__class__.__name__}）", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
