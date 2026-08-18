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
import zipfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


BUNDLE_NAME = "xi-ai-codex-bundle.zip"
CHECKSUM_NAME = f"{BUNDLE_NAME}.sha256"
MAX_API_BYTES = 5 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_EXTRACT_BYTES = 300 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
REQUIRED_PATHS = (
    Path("src/codex_configurator/__main__.py"),
    Path("assets/bundled-models.json"),
    Path("scripts/setup.ps1"),
    Path("scripts/setup.sh"),
)


class BootstrapError(Exception):
    pass


def _require_supported_python(version_info=None) -> None:
    current = sys.version_info if version_info is None else version_info
    if tuple(current[:2]) < (3, 11):
        raise BootstrapError("需要 Python 3.11 或更高版本")


def _request(url: str) -> Request:
    return Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "xi-ai-codex-bootstrap",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _read_limited(response, limit: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise BootstrapError("GitHub 响应超过下载大小限制")
        except ValueError:
            pass
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
    return b"".join(chunks)


def _open_bytes(url: str, *, opener=urlopen, limit: int) -> bytes:
    try:
        with opener(_request(url), timeout=30) as response:
            return _read_limited(response, limit)
    except BootstrapError:
        raise
    except HTTPError as exc:
        raise BootstrapError(f"GitHub 请求失败，HTTP {exc.code}") from exc
    except (URLError, OSError) as exc:
        raise BootstrapError("无法连接 GitHub Releases") from exc


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
    repository: str, version: str, *, opener=urlopen
) -> tuple[str, str, str]:
    payload = _open_bytes(
        _release_api_url(repository, version), opener=opener, limit=MAX_API_BYTES
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


def _download(url: str, destination: Path, *, opener=urlopen, limit: int) -> None:
    content = _open_bytes(url, opener=opener, limit=limit)
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
) -> tuple[str, Path]:
    tag, bundle_url, checksum_url = resolve_release(
        repository, version, opener=opener
    )
    cache_root = cache_root.expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    target = _cache_target(cache_root, tag)

    with tempfile.TemporaryDirectory(prefix="xi-ai-codex-download-") as temp:
        temporary = Path(temp)
        checksum_path = temporary / CHECKSUM_NAME
        _download(checksum_url, checksum_path, opener=opener, limit=1024 * 1024)
        expected_hash = _parse_checksum(checksum_path)
        if not refresh and target.is_dir() and _cached_bundle_is_valid(
            target, expected_hash
        ):
            return tag, target

        bundle_path = temporary / BUNDLE_NAME
        _download(bundle_url, bundle_path, opener=opener, limit=MAX_DOWNLOAD_BYTES)
        _verify_checksum(bundle_path, expected_hash)

        stage = Path(tempfile.mkdtemp(prefix=f".{tag}-", dir=cache_root))
        try:
            safe_extract(bundle_path, stage)
            _validate_bundle_root(stage)
            (stage / ".release-sha256").write_text(
                expected_hash + "\n", encoding="ascii"
            )
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            os.replace(stage, target)
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
        tag, bundle_root = install_release(
            repository,
            version,
            args.cache_dir,
            opener=opener,
            refresh=args.refresh,
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
