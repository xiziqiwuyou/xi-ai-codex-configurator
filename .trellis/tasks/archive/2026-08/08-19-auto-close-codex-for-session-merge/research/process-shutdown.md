# Process Shutdown Research

## Repository Evidence

- `src/codex_configurator/discovery.py` currently classifies only `codex ... app-server` and does not retain parent PIDs.
- `src/codex_configurator/cli.py` rejects `Y` immediately when `desktop_process` is present, before any target write.
- Windows real-machine inspection on 2026-08-19 showed the Store Codex window root as `ChatGPT.exe`; its child `codex.exe` runs `app-server`. The GUI root and backend share the same application installation tree, while the GUI root's parent is outside that tree.
- The current Codex development session itself is a descendant of the detected backend. A real close test from this session would terminate the agent, proving the need for a self-descendant guard and mocked process-control tests.

## Official API Evidence

- Microsoft documents `.CloseMainWindow()` as an orderly UI shutdown request equivalent to closing the main window. The application may refuse or delay it. Forceful kill can lose edited data or allocated process state and should be used only when necessary:
  - https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.process.closemainwindow
- `.WaitForExit(milliseconds)` provides a bounded wait and reports whether the target exited before the timeout:
  - https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.process.waitforexit
- PowerShell `Stop-Process` can target an exact PID, may fail for insufficient permissions, and should not be used by wildcard/name for this feature:
  - https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/stop-process
- Python `os.kill(pid, sig)` sends a signal to one exact POSIX PID. On Windows, non-console signal values use unconditional `TerminateProcess`, so Windows graceful shutdown must use the GUI API instead of `os.kill`:
  - https://docs.python.org/3/library/os.html#os.kill
- Python defines `SIGTERM` as the termination signal and `SIGKILL` as an uncatchable kill signal on Unix:
  - https://docs.python.org/3/library/signal.html

## Design Consequences

1. Use exact verified PIDs and ancestry, never process-name-wide termination.
2. Windows graceful path uses `CloseMainWindow`; POSIX graceful path uses `SIGTERM`.
3. Wait is bounded at 15 seconds and followed by fresh identity/process discovery.
4. The user approved forceful fallback after a 15-second graceful timeout. Revalidate the exact process identity immediately before force and never target by name.
5. No storage write is allowed until the desktop backend is confirmed gone and SQLite readiness checks pass.
