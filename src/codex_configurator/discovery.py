from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .errors import DiscoveryError


VERSION_RE = re.compile(r"(?P<version>\d+\.\d+\.\d+)")
APP_SERVER_RE = re.compile(r"(?:^|\s)app-server(?:\s|$)", re.IGNORECASE)
CODEX_PROCESS_NAMES = {"codex", "codex.exe"}
HOME_MARKERS = ("config.toml", "state_5.sqlite", "sessions", "archived_sessions")


@dataclass(frozen=True)
class DesktopProcess:
    pid: int
    executable: Path
    command_line: str
    source: str


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    name: str
    executable: Path | None
    command_line: str


@dataclass(frozen=True)
class ExecutableCandidate:
    path: Path
    source: str


@dataclass(frozen=True)
class DiscoveryResult:
    codex_home: Path
    executable: Path | None
    version: str | None
    executable_source: str = "not-found"
    codex_home_source: str = "default"
    home_markers: tuple[str, ...] = ()
    home_confidence: str = "low"
    desktop_process: DesktopProcess | None = None
    warnings: tuple[str, ...] = ()


def _resolved_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def _path_key(path: Path) -> str:
    return os.path.normcase(str(_resolved_path(path)))


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _home_markers(path: Path) -> tuple[str, ...]:
    markers: list[str] = []
    for name in HOME_MARKERS:
        candidate = path / name
        try:
            exists = candidate.is_file() if "." in name else candidate.is_dir()
        except OSError:
            exists = False
        if exists:
            markers.append(name)
    return tuple(markers)


def resolve_codex_home_details(
    explicit: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[Path, str, tuple[str, ...], str]:
    environment = os.environ if env is None else env
    if explicit:
        path = _resolved_path(Path(explicit))
        source = "explicit"
    else:
        configured = environment.get("CODEX_HOME", "").strip()
        if configured:
            path = _resolved_path(Path(configured))
            source = "environment"
        else:
            path = _resolved_path((home or Path.home()) / ".codex")
            source = "default"
    markers = _home_markers(path)
    if source in {"explicit", "environment"} or len(markers) >= 2:
        confidence = "high"
    elif markers:
        confidence = "medium"
    else:
        confidence = "low"
    return path, source, markers, confidence


def resolve_codex_home(
    explicit: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    return resolve_codex_home_details(explicit, env=env, home=home)[0]


def _deduplicate_candidates(
    candidates: Sequence[ExecutableCandidate],
) -> list[ExecutableCandidate]:
    unique: list[ExecutableCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _path_key(candidate.path)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _candidate_executable_records(
    *,
    env: Mapping[str, str],
    home: Path,
    platform: str,
    desktop_install_paths: Sequence[Path] = (),
) -> list[ExecutableCandidate]:
    candidates: list[ExecutableCandidate] = []
    search_path = env.get("PATH", "")
    for name in ("codex", "codex.cmd", "codex.exe", "codex.ps1"):
        found = shutil.which(name, path=search_path)
        if found:
            candidates.append(ExecutableCandidate(Path(found), "path"))

    if platform.startswith("win"):
        appdata = Path(env.get("APPDATA", home / "AppData/Roaming"))
        local = Path(env.get("LOCALAPPDATA", home / "AppData/Local"))
        candidates.extend(
            [
                ExecutableCandidate(appdata / "npm/codex.cmd", "npm"),
                ExecutableCandidate(appdata / "npm/codex.ps1", "npm"),
                ExecutableCandidate(home / ".local/bin/codex.exe", "home-local"),
                ExecutableCandidate(local / "Programs/Codex/codex.exe", "desktop-install"),
                ExecutableCandidate(local / "Codex/codex.exe", "desktop-install"),
            ]
        )
    elif platform == "darwin":
        candidates.extend(
            [
                ExecutableCandidate(Path("/opt/homebrew/bin/codex"), "homebrew"),
                ExecutableCandidate(Path("/usr/local/bin/codex"), "package-manager"),
                ExecutableCandidate(home / ".local/bin/codex", "home-local"),
                ExecutableCandidate(
                    Path("/Applications/Codex.app/Contents/Resources/codex"),
                    "desktop-install",
                ),
                ExecutableCandidate(
                    Path("/Applications/Codex.app/Contents/MacOS/codex"),
                    "desktop-install",
                ),
                ExecutableCandidate(
                    home / "Applications/Codex.app/Contents/Resources/codex",
                    "desktop-install",
                ),
            ]
        )
    else:
        candidates.extend(
            [
                ExecutableCandidate(Path("/usr/local/bin/codex"), "package-manager"),
                ExecutableCandidate(Path("/usr/bin/codex"), "package-manager"),
                ExecutableCandidate(home / ".local/bin/codex", "home-local"),
                ExecutableCandidate(Path("/snap/bin/codex"), "package-manager"),
            ]
        )

    candidates.extend(
        ExecutableCandidate(path, "desktop-install") for path in desktop_install_paths
    )
    return _deduplicate_candidates(candidates)


def _candidate_executables(
    *, env: Mapping[str, str], home: Path, platform: str
) -> list[Path]:
    return [
        candidate.path
        for candidate in _candidate_executable_records(
            env=env, home=home, platform=platform
        )
    ]


def find_codex_executable(
    explicit: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
) -> Path | None:
    if explicit:
        candidate = _resolved_path(Path(explicit))
        if not _is_file(candidate):
            raise DiscoveryError(f"Codex executable does not exist: {candidate}")
        return candidate

    environment = os.environ if env is None else env
    user_home = home or Path.home()
    current_platform = platform or sys.platform
    for candidate in _candidate_executable_records(
        env=environment, home=user_home, platform=current_platform
    ):
        if _is_file(candidate.path):
            return _resolved_path(candidate.path)
    return None


def codex_command(executable: Path, *args: str) -> list[str]:
    suffix = executable.suffix.lower()
    if suffix == ".ps1":
        shell = shutil.which("powershell.exe") or shutil.which("pwsh") or "powershell"
        return [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(executable),
            *args,
        ]
    if suffix in {".cmd", ".bat"}:
        return [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            str(executable),
            *args,
        ]
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


def classify_desktop_processes(
    records: Sequence[ProcessRecord], *, source: str
) -> tuple[DesktopProcess, ...]:
    matches: list[DesktopProcess] = []
    for record in records:
        if record.executable is None:
            continue
        process_name = Path(record.name).name.lower()
        executable_name = record.executable.name.lower()
        if (
            process_name not in CODEX_PROCESS_NAMES
            and executable_name not in CODEX_PROCESS_NAMES
        ):
            continue
        if not APP_SERVER_RE.search(record.command_line):
            continue
        matches.append(
            DesktopProcess(
                pid=record.pid,
                executable=_resolved_path(record.executable),
                command_line=record.command_line,
                source=source,
            )
        )
    return tuple(sorted(matches, key=lambda item: item.pid))


def _powershell_executable() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("pwsh")


def _windows_process_records(*, runner=subprocess.run) -> list[ProcessRecord]:
    shell = _powershell_executable()
    if not shell:
        raise OSError("PowerShell is unavailable")
    script = (
        "$items = @(Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -ieq 'codex.exe' } | "
        "Select-Object ProcessId,Name,ExecutablePath,CommandLine); "
        "$items | ConvertTo-Json -Compress"
    )
    result = runner(
        [shell, "-NoProfile", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if not result.stdout.strip():
        return []
    document = json.loads(result.stdout)
    items = document if isinstance(document, list) else [document]
    records: list[ProcessRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = item.get("ProcessId")
        executable = item.get("ExecutablePath")
        if not isinstance(pid, int) or not isinstance(executable, str) or not executable:
            continue
        records.append(
            ProcessRecord(
                pid=pid,
                name=str(item.get("Name") or ""),
                executable=Path(executable),
                command_line=str(item.get("CommandLine") or ""),
            )
        )
    return records


def _posix_process_records(
    *, platform: str | None = None, runner=subprocess.run
) -> list[ProcessRecord]:
    current_platform = platform or sys.platform
    result = runner(
        ["ps", "-axo", "pid=,comm=,args="],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    records: list[ProcessRecord] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) < 3:
            continue
        raw_pid, command, arguments = fields
        try:
            pid = int(raw_pid)
        except ValueError:
            continue
        executable = Path(command)
        first_argument = arguments.split(None, 1)[0].strip('"\'')
        possible = Path(first_argument)
        if possible.name.lower() in CODEX_PROCESS_NAMES and possible.is_absolute():
            executable = possible
        if current_platform.startswith("linux"):
            try:
                executable = Path(os.readlink(f"/proc/{pid}/exe"))
            except OSError:
                pass
        if not executable.is_absolute():
            found = shutil.which(str(executable))
            if found:
                executable = Path(found)
        records.append(
            ProcessRecord(
                pid=pid,
                name=Path(command).name,
                executable=executable,
                command_line=arguments,
            )
        )
    return records


def discover_running_codex_processes(
    *, platform: str | None = None, runner=subprocess.run
) -> tuple[tuple[DesktopProcess, ...], tuple[str, ...]]:
    current_platform = platform or sys.platform
    try:
        if current_platform.startswith("win"):
            records = _windows_process_records(runner=runner)
            source = "windows-process"
        else:
            records = _posix_process_records(platform=current_platform, runner=runner)
            source = "posix-process"
        return classify_desktop_processes(records, source=source), ()
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError):
        return (), ("Unable to inspect running Codex desktop processes",)


def discover_windows_appx_candidates(
    *, runner=subprocess.run
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    shell = _powershell_executable()
    if not shell:
        return (), ()
    script = (
        "$items = @(Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction SilentlyContinue | "
        "ForEach-Object { Join-Path $_.InstallLocation 'app\\resources\\codex.exe' }); "
        "$items | ConvertTo-Json -Compress"
    )
    try:
        result = runner(
            [shell, "-NoProfile", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if not result.stdout.strip():
            return (), ()
        document = json.loads(result.stdout)
        values = document if isinstance(document, list) else [document]
        paths = tuple(Path(value) for value in values if isinstance(value, str) and value)
        return paths, ()
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError):
        return (), ("Unable to inspect the registered Windows Codex application",)


def _discover_implicit_executable(
    candidates: Sequence[ExecutableCandidate],
    *,
    runner=subprocess.run,
) -> tuple[Path | None, str | None, str, list[str]]:
    warnings: list[str] = []
    for candidate in candidates:
        if not _is_file(candidate.path):
            continue
        path = _resolved_path(candidate.path)
        try:
            version = detect_codex_version(path, runner=runner)
        except DiscoveryError:
            warnings.append(
                f"Skipped a non-runnable Codex candidate from {candidate.source}: {path}"
            )
            continue
        return path, version, candidate.source, warnings
    return None, None, "not-found", warnings


def discover(
    *,
    codex_home: str | Path | None = None,
    codex_bin: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
    version_runner=subprocess.run,
    inspection_runner=subprocess.run,
    process_detector: Callable[..., tuple[tuple[DesktopProcess, ...], tuple[str, ...]]]
    | None = None,
    appx_detector: Callable[..., tuple[tuple[Path, ...], tuple[str, ...]]]
    | None = None,
) -> DiscoveryResult:
    environment = os.environ if env is None else env
    user_home = home or Path.home()
    current_platform = platform or sys.platform
    resolved_home, home_source, markers, confidence = resolve_codex_home_details(
        codex_home, env=environment, home=user_home
    )

    detect_processes = process_detector or discover_running_codex_processes
    processes, process_warnings = detect_processes(
        platform=current_platform, runner=inspection_runner
    )
    desktop_process = processes[0] if processes else None
    warnings = list(process_warnings)
    if len(processes) > 1:
        warnings.append(
            f"Detected {len(processes)} Codex desktop backends; using PID {desktop_process.pid}"
        )

    desktop_install_paths: tuple[Path, ...] = ()
    if current_platform.startswith("win"):
        detect_appx = appx_detector or discover_windows_appx_candidates
        desktop_install_paths, appx_warnings = detect_appx(runner=inspection_runner)
        warnings.extend(appx_warnings)

    if codex_bin:
        executable = _resolved_path(Path(codex_bin))
        if not _is_file(executable):
            raise DiscoveryError(f"Codex executable does not exist: {executable}")
        version = detect_codex_version(executable, runner=version_runner)
        executable_source = "explicit"
    else:
        candidates = _candidate_executable_records(
            env=environment,
            home=user_home,
            platform=current_platform,
            desktop_install_paths=desktop_install_paths,
        )
        if desktop_process is not None:
            candidates = _deduplicate_candidates(
                [
                    *candidates,
                    ExecutableCandidate(desktop_process.executable, "running-process"),
                ]
            )
        executable, version, executable_source, candidate_warnings = (
            _discover_implicit_executable(candidates, runner=version_runner)
        )
        warnings.extend(candidate_warnings)

    return DiscoveryResult(
        codex_home=resolved_home,
        executable=executable,
        version=version,
        executable_source=executable_source,
        codex_home_source=home_source,
        home_markers=markers,
        home_confidence=confidence,
        desktop_process=desktop_process,
        warnings=tuple(warnings),
    )


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
