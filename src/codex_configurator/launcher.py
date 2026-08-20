from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .discovery import DiscoveryResult
from .errors import LaunchError


@dataclass(frozen=True)
class CodexLaunchResult:
    """Evidence that a detached Codex start request was accepted."""

    target: Path
    pid: int


APP_USER_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]+![A-Za-z0-9._-]+$")


def _is_windows_store_path(target: Path) -> bool:
    normalized = str(target).replace("/", "\\").lower()
    return "\\windowsapps\\" in normalized


def _registered_codex_app_id(*, runner=subprocess.run) -> str | None:
    """Read the registered Store application ID without touching its binary."""

    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if powershell is None:
        return None
    script = (
        "$package = @(Get-AppxPackage -Name 'OpenAI.Codex' "
        "-ErrorAction SilentlyContinue | Select-Object -First 1)[0];"
        "if ($null -eq $package) { exit 2 };"
        "$manifest = Get-AppxPackageManifest -Package $package;"
        "$applications = @($manifest.Package.Applications.Application);"
        "$application = $applications | Where-Object Id -eq 'App' "
        "| Select-Object -First 1;"
        "if ($null -eq $application) { "
        "$application = $applications | Select-Object -First 1 };"
        "if ($null -eq $application) { exit 3 };"
        "Write-Output ($package.PackageFamilyName + '!' + $application.Id)"
    )
    try:
        result = runner(
            [powershell, "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    if int(getattr(result, "returncode", 1)) != 0:
        return None
    value = str(getattr(result, "stdout", "")).strip().splitlines()
    app_id = value[-1].strip() if value else ""
    return app_id if APP_USER_MODEL_ID_RE.fullmatch(app_id) else None


def select_launch_target(
    discovery: DiscoveryResult,
    *,
    was_closed: bool,
) -> Path | None:
    """Choose a desktop executable using only discovery evidence.

    A desktop process that setup closed is the strongest evidence because its
    root executable was identity-verified before shutdown. When no process was
    closed, only an explicit executable or a desktop-install candidate is
    eligible. PATH/npm CLI candidates are intentionally excluded.
    """

    process = discovery.desktop_process
    if was_closed and process is not None:
        return process.root_executable or process.executable
    if process is not None:
        return None
    if discovery.executable is None:
        return None
    if discovery.executable_source in {"explicit", "desktop-install"}:
        return discovery.executable
    return None


def launch_codex(
    discovery: DiscoveryResult,
    *,
    was_closed: bool,
    platform: str | None = None,
    runner: Callable = subprocess.Popen,
    registered_app_id_fn: Callable[[], str | None] | None = None,
) -> CodexLaunchResult:
    """Start an eligible Codex desktop target in a detached process."""

    target = select_launch_target(discovery, was_closed=was_closed)
    if target is None:
        raise LaunchError("未找到可自动启动的 Codex 桌面程序，请手动启动 Codex")

    current_platform = platform or sys.platform
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if current_platform.startswith("win"):
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    else:
        kwargs["start_new_session"] = True

    command = [str(target)]
    if current_platform.startswith("win") and _is_windows_store_path(target):
        app_id_reader = registered_app_id_fn or _registered_codex_app_id
        app_id = app_id_reader()
        if isinstance(app_id, str) and APP_USER_MODEL_ID_RE.fullmatch(app_id):
            explorer = shutil.which("explorer.exe") or "explorer.exe"
            command = [explorer, f"shell:AppsFolder\\{app_id}"]

    try:
        process = runner(command, **kwargs)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise LaunchError(
            f"Codex 启动请求失败，请手动启动 Codex（目标不可用或权限不足）"
        ) from exc

    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        raise LaunchError("Codex 启动请求未返回有效进程 PID，请手动启动 Codex")
    return CodexLaunchResult(target=target, pid=pid)
