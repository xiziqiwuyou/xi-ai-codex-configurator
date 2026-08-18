from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .discovery import (
    DesktopProcess,
    ProcessRecord,
    classify_desktop_processes,
    inspect_process_records,
)
from .errors import DesktopControlError


GRACEFUL_TIMEOUT_SECONDS = 15.0
FORCE_TIMEOUT_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.25
SIGTERM = getattr(signal, "SIGTERM", 15)
SIGKILL = getattr(signal, "SIGKILL", 9)


@dataclass(frozen=True)
class DesktopCloseResult:
    root_pid: int
    forced: bool


def _path_identity(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser().absolute()
    return os.path.normcase(str(resolved))


def _verified_backend(
    expected: DesktopProcess, records: Sequence[ProcessRecord]
) -> DesktopProcess | None:
    raw = next((record for record in records if record.pid == expected.pid), None)
    if raw is None:
        return None
    current = next(
        (
            process
            for process in classify_desktop_processes(records, source=expected.source)
            if process.pid == expected.pid
        ),
        None,
    )
    if current is None:
        raise DesktopControlError("Codex 进程身份已变化，拒绝继续关闭")
    if (
        _path_identity(current.executable) != _path_identity(expected.executable)
        or current.command_line.strip() != expected.command_line.strip()
    ):
        raise DesktopControlError("Codex 进程身份已变化，拒绝继续关闭")
    expected_root_is_running = any(
        record.pid == expected.root_pid for record in records
    )
    if (
        expected.root_pid is not None
        and current.root_pid != expected.root_pid
        and expected_root_is_running
    ):
        raise DesktopControlError("Codex 根进程身份已变化，拒绝继续关闭")
    if (
        expected.root_executable is not None
        and current.root_executable is not None
        and _path_identity(current.root_executable)
        != _path_identity(expected.root_executable)
        and expected_root_is_running
    ):
        raise DesktopControlError("Codex 根进程身份已变化，拒绝继续关闭")
    return current


def _ensure_external_caller(
    caller_pid: int, process: DesktopProcess, records: Sequence[ProcessRecord]
) -> None:
    by_pid = {record.pid: record for record in records}
    if caller_pid not in by_pid:
        raise DesktopControlError(
            "无法验证配置脚本的进程树；请从系统 PowerShell/Terminal 重新运行"
        )
    protected = {process.pid, process.root_pid or process.pid}
    current_pid: int | None = caller_pid
    visited: set[int] = set()
    while current_pid is not None and current_pid not in visited:
        if current_pid in protected:
            raise DesktopControlError(
                "配置脚本正在 Codex 进程树内运行；请从系统 PowerShell/Terminal 重新运行"
            )
        visited.add(current_pid)
        record = by_pid.get(current_pid)
        current_pid = record.parent_pid if record is not None else None


def _snapshot_records(
    snapshot_fn: Callable[[], Sequence[ProcessRecord]],
) -> tuple[ProcessRecord, ...]:
    try:
        return tuple(snapshot_fn())
    except DesktopControlError:
        raise
    except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
        raise DesktopControlError(
            "无法重新检查 Codex 进程，已停止配置"
        ) from exc


def _run_windows_action(
    script: str,
    *,
    runner,
    powershell: str | None,
) -> int:
    shell = powershell or shutil.which("powershell.exe") or shutil.which("pwsh")
    if shell is None:
        shell = "powershell.exe"
    try:
        result = runner(
            [shell, "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DesktopControlError("无法控制 Codex 桌面进程") from exc
    return int(result.returncode)


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _request_windows_close(
    process: DesktopProcess, *, runner, powershell: str | None
) -> bool:
    root_pid = process.root_pid or process.pid
    root_executable = process.root_executable or process.executable
    script = (
        "$ErrorActionPreference='Stop';"
        f"$record=Get-CimInstance Win32_Process -Filter \"ProcessId={root_pid}\";"
        "if($null -eq $record -or $record.ExecutablePath -ine "
        f"{_powershell_literal(str(root_executable))}){{exit 4}};"
        f"$process=Get-Process -Id {root_pid} -ErrorAction Stop;"
        "if($process.CloseMainWindow()){exit 0}else{exit 3}"
    )
    return_code = _run_windows_action(
        script, runner=runner, powershell=powershell
    )
    if return_code == 0:
        return True
    if return_code == 3:
        return False
    if return_code == 4:
        raise DesktopControlError("Codex 根进程身份已变化，拒绝继续关闭")
    raise DesktopControlError("无法请求 Codex 正常退出，可能权限不足")


def _force_windows_close(
    process: DesktopProcess,
    pids: Sequence[int],
    *,
    runner,
    powershell: str | None,
) -> None:
    joined = ",".join(str(pid) for pid in pids)
    backend_path = _powershell_literal(str(process.executable))
    backend_command = _powershell_literal(process.command_line)
    root_pid = process.root_pid or process.pid
    root_path = _powershell_literal(
        str(process.root_executable or process.executable)
    )
    script = (
        "$ErrorActionPreference='Stop';"
        f"$backend=Get-CimInstance Win32_Process -Filter \"ProcessId={process.pid}\";"
        "if($null -eq $backend -or $backend.ExecutablePath -ine "
        f"{backend_path} -or $backend.CommandLine -cne {backend_command}){{exit 4}};"
        f"$root=Get-CimInstance Win32_Process -Filter \"ProcessId={root_pid}\";"
        f"if($null -eq $root -or $root.ExecutablePath -ine {root_path}){{exit 4}};"
        f"$ids=@({joined});"
        "Stop-Process -Id $ids -Force -ErrorAction Stop"
    )
    return_code = _run_windows_action(
        script, runner=runner, powershell=powershell
    )
    if return_code == 4:
        raise DesktopControlError("Codex 进程身份已变化，拒绝强制终止")
    if return_code != 0:
        raise DesktopControlError("无法强制终止 Codex，可能权限不足")


def _wait_for_backend_exit(
    expected: DesktopProcess,
    *,
    snapshot_fn: Callable[[], Sequence[ProcessRecord]],
    timeout: float,
    monotonic: Callable[[], float],
    sleep_fn: Callable[[float], None],
) -> bool:
    deadline = monotonic() + max(timeout, 0.0)
    while True:
        if _verified_backend(expected, _snapshot_records(snapshot_fn)) is None:
            return True
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        sleep_fn(min(POLL_INTERVAL_SECONDS, remaining))


def close_codex_desktop(
    process: DesktopProcess,
    *,
    platform: str | None = None,
    caller_pid: int | None = None,
    snapshot_fn: Callable[[], Sequence[ProcessRecord]] | None = None,
    action_runner=subprocess.run,
    signal_sender=os.kill,
    monotonic: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
    graceful_timeout: float = GRACEFUL_TIMEOUT_SECONDS,
    force_timeout: float = FORCE_TIMEOUT_SECONDS,
    powershell: str | None = None,
) -> DesktopCloseResult:
    current_platform = platform or sys.platform
    take_snapshot = snapshot_fn or (
        lambda: inspect_process_records(
            platform=current_platform, runner=action_runner
        )
    )
    initial_records = _snapshot_records(take_snapshot)
    current = _verified_backend(process, initial_records)
    if current is None:
        return DesktopCloseResult(root_pid=process.root_pid or process.pid, forced=False)

    _ensure_external_caller(caller_pid or os.getpid(), current, initial_records)
    root_pid = current.root_pid or current.pid

    if current_platform.startswith("win"):
        graceful_requested = _request_windows_close(
            current, runner=action_runner, powershell=powershell
        )
    else:
        try:
            signal_sender(root_pid, SIGTERM)
        except OSError as exc:
            raise DesktopControlError("无法请求 Codex 正常退出，可能权限不足") from exc
        graceful_requested = True

    if graceful_requested and _wait_for_backend_exit(
        process,
        snapshot_fn=take_snapshot,
        timeout=graceful_timeout,
        monotonic=monotonic,
        sleep_fn=sleep_fn,
    ):
        return DesktopCloseResult(root_pid=root_pid, forced=False)

    force_records = _snapshot_records(take_snapshot)
    force_target = _verified_backend(process, force_records)
    if force_target is None:
        return DesktopCloseResult(root_pid=root_pid, forced=False)
    _ensure_external_caller(caller_pid or os.getpid(), force_target, force_records)

    force_pids = list(
        dict.fromkeys((force_target.root_pid or force_target.pid, force_target.pid))
    )
    if current_platform.startswith("win"):
        _force_windows_close(
            force_target,
            force_pids,
            runner=action_runner,
            powershell=powershell,
        )
    else:
        for pid in force_pids:
            try:
                signal_sender(pid, SIGKILL)
            except ProcessLookupError:
                continue
            except OSError as exc:
                raise DesktopControlError(
                    "无法强制终止 Codex，可能权限不足"
                ) from exc

    if not _wait_for_backend_exit(
        process,
        snapshot_fn=take_snapshot,
        timeout=force_timeout,
        monotonic=monotonic,
        sleep_fn=sleep_fn,
    ):
        raise DesktopControlError("强制终止后 Codex 仍在运行，已停止配置")
    return DesktopCloseResult(root_pid=root_pid, forced=True)
