#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path


BUNDLE_NAME = "xi-ai-codex-bundle.zip"
BOOTSTRAP_NAME = "xi-ai-codex-bootstrap.py"
MANIFEST_NAME = "xi-ai-codex-release.json"
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ROOT_FILES = ("README.md", "pyproject.toml")
ROOT_DIRECTORIES = ("src", "assets")
SETUP_FILES = ("scripts/setup.ps1", "scripts/setup.sh")
# These are published at /xi-ai-codex/ so a user can keep one stable command
# while the versioned bootstrap and bundle continue to be immutable.
ENTRY_FILES = {
    "setup.ps1": "scripts/remote_setup.ps1",
    "setup.sh": "scripts/remote_setup.sh",
}
ENTRY_NAMES = (
    "setup.ps1",
    "setup.ps1.sha256",
    "setup.sh",
    "setup.sh.sha256",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_release_files(root: Path):
    for relative in (*ROOT_FILES, *SETUP_FILES):
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"Missing release file: {relative}")
        yield path, Path(relative)
    for directory in ROOT_DIRECTORIES:
        source = root / directory
        if not source.is_dir():
            raise FileNotFoundError(f"Missing release directory: {directory}")
        for path in sorted(source.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix in {".pyc", ".pyo"}:
                continue
            yield path, path.relative_to(root)


def _write_checksum(path: Path) -> Path:
    checksum = path.with_name(path.name + ".sha256")
    checksum.write_text(f"{_sha256(path)}  {path.name}\n", encoding="ascii")
    return checksum


def _write_entry_assets(root: Path, output: Path) -> list[Path]:
    generated: list[Path] = []
    for name, relative in ENTRY_FILES.items():
        source = root / relative
        if not source.is_file():
            raise FileNotFoundError(f"Missing fixed entry source: {relative}")
        destination = output / name
        shutil.copy2(source, destination)
        generated.append(destination)
        generated.append(_write_checksum(destination))
    return generated


def build_release(root: Path, output: Path, version: str) -> list[Path]:
    if not VERSION_RE.fullmatch(version):
        raise ValueError("Release version contains unsupported characters")
    root = root.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle = output / BUNDLE_NAME
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, relative in _iter_release_files(root):
            archive.write(source, relative.as_posix())

    bootstrap = output / BOOTSTRAP_NAME
    shutil.copy2(root / "scripts/bootstrap.py", bootstrap)
    bundle_checksum = _write_checksum(bundle)
    bootstrap_checksum = _write_checksum(bootstrap)
    manifest = output / MANIFEST_NAME
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": version,
                "bundle": {
                    "name": BUNDLE_NAME,
                    "sha256": _sha256(bundle),
                    "size": bundle.stat().st_size,
                },
                "bootstrap": {
                    "name": BOOTSTRAP_NAME,
                    "sha256": _sha256(bootstrap),
                    "size": bootstrap.stat().st_size,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    entry_assets = _write_entry_assets(root, output)
    return [
        bundle,
        bundle_checksum,
        bootstrap,
        bootstrap_checksum,
        manifest,
        *entry_assets,
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Xi-AI Codex release assets.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    for path in build_release(root, args.output, args.version):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
