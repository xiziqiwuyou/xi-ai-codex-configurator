from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .errors import DiscoveryError


VERSION_RE = re.compile(r"(?P<version>\d+\.\d+\.\d+)")


@dataclass(frozen=True)
class DiscoveryResult:
    codex_home: Path
    executable: Path | None
    version: str | None


def resolve_codex_home(
    explicit: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    environment = os.environ if env is None else env
    if explicit:
        return Path(explicit).expanduser().resolve()
    configured = environment.get("CODEX_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return ((home or Path.home()) / ".codex").resolve()


def _candidate_executables(
    *, env: Mapping[str, str], home: Path, platform: str
) -> list[Path]:
    candidates: list[Path] = []
    for name in ("codex", "codex.cmd", "codex.exe", "codex.ps1"):
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            candidates.append(Path(found))

    if platform.startswith("win"):
        appdata = Path(env.get("APPDATA", home / "AppData/Roaming"))
        local = Path(env.get("LOCALAPPDATA", home / "AppData/Local"))
        candidates.extend(
            [
                appdata / "npm/codex.cmd",
                appdata / "npm/codex.ps1",
                local / "Programs/Codex/codex.exe",
                local / "Codex/codex.exe",
            ]
        )
    elif platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Codex.app/Contents/MacOS/codex"),
                home / "Applications/Codex.app/Contents/MacOS/codex",
            ]
        )
    else:
        candidates.extend([Path("/usr/local/bin/codex"), home / ".local/bin/codex"])

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def find_codex_executable(
    explicit: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
) -> Path | None:
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if not candidate.is_file():
            raise DiscoveryError(f"Codex executable does not exist: {candidate}")
        return candidate

    environment = os.environ if env is None else env
    user_home = home or Path.home()
    current_platform = platform or sys.platform
    for candidate in _candidate_executables(
        env=environment, home=user_home, platform=current_platform
    ):
        if candidate.is_file():
            return candidate.resolve()
    return None


def codex_command(executable: Path, *args: str) -> list[str]:
    suffix = executable.suffix.lower()
    if suffix == ".ps1":
        shell = shutil.which("powershell.exe") or shutil.which("pwsh") or "powershell"
        return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(executable), *args]
    if suffix in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(executable), *args]
    return [str(executable), *args]


def detect_codex_version(
    executable: Path,
    *,
    runner=subprocess.run,
) -> str:
    try:
        result = runner(
            codex_command(executable, "--version"),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DiscoveryError(f"Unable to run Codex: {exc}") from exc
    match = VERSION_RE.search(result.stdout or result.stderr)
    if not match:
        raise DiscoveryError("Codex version output was not recognized")
    return match.group("version")


def discover(
    *,
    codex_home: str | Path | None = None,
    codex_bin: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
) -> DiscoveryResult:
    resolved_home = resolve_codex_home(codex_home, env=env, home=home)
    executable = find_codex_executable(
        codex_bin, env=env, home=home, platform=platform
    )
    version = detect_codex_version(executable) if executable else None
    return DiscoveryResult(resolved_home, executable, version)


def run_codex_json(
    executable: Path,
    args: Sequence[str],
    *,
    runner=subprocess.run,
) -> str:
    try:
        result = runner(
            codex_command(executable, *args),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DiscoveryError(f"Codex command failed: {exc}") from exc
    return result.stdout
