# Technical design

## Boundaries

Add a small `launcher` module for the post-commit desktop start operation. Keep
process shutdown in `desktop_control.py`, discovery in `discovery.py`, and file
mutation in `transaction.py`. The CLI owns the lifecycle decision because it
knows whether migration closed a process and whether the setup was a dry run.

## Launch contract

```python
@dataclass(frozen=True)
class CodexLaunchResult:
    target: Path
    pid: int

def launch_codex(
    discovery: DiscoveryResult,
    *,
    was_closed: bool,
    platform: str | None = None,
    runner: Callable = subprocess.Popen,
) -> CodexLaunchResult:
    ...
```

Target selection is deliberately evidence-based. A process that was closed
provides the strongest target (`root_executable`, then backend executable). If
the desktop is still present, the caller skips launching. With no running
desktop, only `desktop-install` or explicit executable evidence is eligible;
ordinary PATH/npm CLI candidates are not launched automatically.

The runner receives a single executable argument, `stdin=DEVNULL`,
`stdout=DEVNULL`, and `stderr=DEVNULL`. Windows adds
`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` and POSIX adds
`start_new_session=True`. The returned PID is sufficient evidence that the
start request was accepted; readiness polling is intentionally not required.

Windows Store targets under `WindowsApps` are an exception: their binary path
can be observed but direct execution is access-denied. For those targets, read
the registered `OpenAI.Codex` application ID through PowerShell and start
`explorer.exe shell:AppsFolder\\<AUMID>` with the same detached handles. If the
registration lookup is unavailable, fall back to the verified path and report a
normal launch error if the operating system rejects it.

## CLI flow

After `apply_setup()` and `validate_installed()` succeed, call a narrow helper
that either reports an already-running client, launches an eligible target, or
prints a manual fallback. A launch exception is reported as a post-commit
failure and returns `1`; it never invokes rollback because the transaction is
already complete. Inject the launcher into `main()` for deterministic tests.

## PowerShell lifecycle

Use the observable `$PSScriptRoot` value: it is populated for `-File` execution
and empty for the current fixed entry's `Invoke-Expression` execution. Replace
unconditional final `exit` with a helper that returns from IEX and exits only
the child/file host. Preserve non-zero status in `-File` mode. The Python setup
result and launch status are printed before the success exit.

## Compatibility and rollback

The Python API gains only optional keyword injection (`codex_launcher`) so
existing callers remain source-compatible. No TOML, SQLite, rollout, backup,
endpoint, or release manifest format changes are required. If the launcher is
unavailable, users can manually start Codex and the committed backup remains
valid.

## Security considerations

Do not pass the API token in a process argument or environment variable. Do not
log command lines, conversation paths, or response bodies. Only paths already
validated by discovery are eligible for automatic launch. Never use a broad
process-name kill or shell command assembled from untrusted input.
