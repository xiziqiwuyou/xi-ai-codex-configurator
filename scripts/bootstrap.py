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
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


BUNDLE_NAME = "xi-ai-codex-bundle.zip"
CHECKSUM_NAME = f"{BUNDLE_NAME}.sha256"
MAX_API_BYTES = 5 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_EXTRACT_BYTES = 300 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
DOWNLOAD_ATTEMPTS = 3
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ASSET_API_PATH_RE = re.compile(
    r"^/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/assets/[1-9][0-9]*$"
)
REQUIRED_PATHS = (
    Path("src/codex_configurator/__main__.py"),
    Path("assets/bundled-models.json"),
    Path("scripts/setup.ps1"),
    Path("scripts/setup.sh"),
)


class BootstrapError(Exception):
    pass


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


def _is_asset_api_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.github.com"
        and ASSET_API_PATH_RE.fullmatch(parsed.path) is not None
        and not parsed.query
        and not parsed.fragment
    )


def _request(url: str) -> Request:
    accept = (
        "application/octet-stream"
        if _is_asset_api_url(url)
        else "application/vnd.github+json"
    )
    return Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "xi-ai-codex-bootstrap",
            "X-GitHub-Api-Version": "2022-11-28",
        },
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
                raise BootstrapError("GitHub 响应超过下载大小限制")
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
            raise BootstrapError("GitHub 响应超过下载大小限制")
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
        raise BootstrapError("GitHub 响应长度与 Content-Length 不一致")
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
    opener=urlopen,
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
            raise BootstrapError(f"GitHub 请求失败，HTTP {exc.code}") from exc
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
    raise BootstrapError("多次重试后仍无法连接 GitHub Releases") from last_error


def _validate_repository(repository: str) -> str:
    value = repository.strip()
    if not REPOSITORY_RE.fullmatch(value):
        raise BootstrapError("GitHub 仓库必须使用 OWNER/REPO 格式")
    return value


def _validate_version(version: str) -> str:
    value = version.strip()
    if value != "latest" and not TAG_RE.fullmatch(value):
        raise BootstrapError("GitHub Release 版本包含不支持的字符")
    return value


def _release_api_url(repository: str, version: str) -> str:
    if version == "latest":
        return f"https://api.github.com/repos/{repository}/releases/latest"
    return (
        f"https://api.github.com/repos/{repository}/releases/tags/"
        f"{quote(version, safe='')}"
    )


def _asset_url(release: dict, name: str) -> str:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise BootstrapError("GitHub Release 中缺少 assets 数组")
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") != name:
            continue
        api_url = asset.get("url")
        if isinstance(api_url, str):
            if not _is_asset_api_url(api_url):
                raise BootstrapError(f"GitHub Release 资产 API URL 无效：{name}")
            return api_url
        url = asset.get("browser_download_url")
        if not isinstance(url, str):
            break
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "github.com",
            "objects.githubusercontent.com",
            "github-releases.githubusercontent.com",
        }:
            raise BootstrapError(f"GitHub Release 资产 URL 无效：{name}")
        return url
    raise BootstrapError(f"GitHub Release 缺少资产：{name}")


def resolve_release(
    repository: str,
    version: str,
    *,
    opener=urlopen,
    progress: DownloadProgressCallback | None = None,
) -> tuple[str, str, str]:
    payload = _open_bytes(
        _release_api_url(repository, version),
        opener=opener,
        limit=MAX_API_BYTES,
        progress=progress,
        stage="Release 元数据",
    )
    try:
        release = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("GitHub 返回了无效的 Release 元数据") from exc
    if not isinstance(release, dict):
        raise BootstrapError("GitHub 返回了无效的 Release 元数据")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
        raise BootstrapError("GitHub Release 标签无效")
    if version != "latest" and tag != version:
        raise BootstrapError("GitHub Release 标签与请求的版本不一致")
    return tag, _asset_url(release, BUNDLE_NAME), _asset_url(release, CHECKSUM_NAME)


def _download(
    url: str,
    destination: Path,
    *,
    opener=urlopen,
    limit: int,
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
    if len(fields) != 2 or not HASH_RE.fullmatch(fields[0]):
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
    repository: str,
    version: str,
    cache_root: Path,
    *,
    opener=urlopen,
    refresh: bool = False,
    progress: DownloadProgressCallback | None = None,
) -> tuple[str, Path]:
    tag, bundle_url, checksum_url = resolve_release(
        repository, version, opener=opener, progress=progress
    )
    cache_root = cache_root.expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    target = _cache_target(cache_root, tag)

    with tempfile.TemporaryDirectory(prefix="xi-ai-codex-download-") as temp:
        temporary = Path(temp)
        checksum_path = temporary / CHECKSUM_NAME
        _download(
            checksum_url,
            checksum_path,
            opener=opener,
            limit=1024 * 1024,
            progress=progress,
            stage="下载 Release 校验文件",
        )
        expected_hash = _parse_checksum(checksum_path)
        if not refresh and target.is_dir() and _cached_bundle_is_valid(
            target, expected_hash
        ):
            _emit_progress(
                progress,
                DownloadProgress("已验证缓存，跳过程序包下载", "complete"),
            )
            return tag, target

        bundle_path = temporary / BUNDLE_NAME
        _download(
            bundle_url,
            bundle_path,
            opener=opener,
            limit=MAX_DOWNLOAD_BYTES,
            progress=progress,
            stage="下载 Release 程序包",
        )
        _emit_progress(progress, DownloadProgress("校验 SHA-256", "start"))
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
        description="下载、校验并运行 Xi-AI Codex GitHub Release。"
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="公开 GitHub 仓库，格式为 OWNER/REPO",
    )
    parser.add_argument(
        "--version",
        default=os.environ.get("XI_AI_CODEX_VERSION"),
        help="指定 Release 标签；如需最新版，必须显式传入 latest",
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
    opener=urlopen,
    runner=subprocess.run,
    progress: DownloadProgressCallback | None = None,
) -> int:
    parser = build_parser()
    args, setup_args = parser.parse_known_args(argv)
    if setup_args and setup_args[0] == "--":
        setup_args = setup_args[1:]
    try:
        _require_supported_python()
        if not args.repo:
            raise BootstrapError("请传入 --repo OWNER/REPO 或设置 GITHUB_REPOSITORY")
        if not args.version:
            raise BootstrapError(
                "请传入 --version TAG 或设置 XI_AI_CODEX_VERSION；"
                "如需最新版，请显式使用 latest"
            )
        repository = _validate_repository(args.repo)
        version = _validate_version(args.version)
        if not args.configure and "--detect-only" not in setup_args:
            setup_args.insert(0, "--detect-only")
        progress_callback = progress if progress is not None else BootstrapProgress()
        tag, bundle_root = install_release(
            repository,
            version,
            args.cache_dir,
            opener=opener,
            refresh=args.refresh,
            progress=progress_callback,
        )
        print(f"GitHub Release 校验通过：{repository}@{tag}")
        print(f"本地程序包：{bundle_root}")
        return run_setup(bundle_root, setup_args, runner=runner)
    except BootstrapError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"错误：本地 Release 操作失败（{exc.__class__.__name__}）", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
