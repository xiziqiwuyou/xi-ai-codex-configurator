# Technical Design

## Current Boundary

`discovery.py` currently identifies only `codex ... app-server` and returns one `DesktopProcess`. `cli.py` treats an active desktop process as a hard migration error. Storage mutation remains correctly isolated in `transaction.py`.

Opening Codex can create useful `~/.codex` markers and expose the desktop executable, but the process executable is not an authoritative configuration-home source. The existing `--codex-home` -> `CODEX_HOME` -> `~/.codex` contract remains unchanged.

## Proposed Components

### Process metadata

Extend process records with parent PID information and derive a verified desktop root for each `app-server`:

- backend PID and executable;
- GUI/root PID and executable when it can be proven to share the same application install root;
- process source and command line;
- enough ancestry information to determine whether the current script is a descendant of the target backend.

Windows discovery must include the `ChatGPT.exe` parent chain used by the Codex Store package. POSIX discovery changes from `ps pid,comm,args` to `ps pid,ppid,comm,args`.

### Desktop control module

Add a small process-control module separate from discovery and transaction code. Its public operation accepts one verified `DesktopProcess`, the caller PID, platform, injected runner/signal/wait functions, and a timeout.

Windows flow:

1. Re-read the target PID and ancestry through PowerShell/CIM.
2. Verify the backend command still contains `app-server`, its executable still matches discovery, and the chosen GUI root remains inside the same application install root.
3. Reject when the caller is a descendant of the backend.
4. Call `.CloseMainWindow()` on the exact GUI root and wait up to 15 seconds.
5. If the backend remains alive, re-read and revalidate the exact PID, executable, command line and ancestry, then use `Stop-Process -Id <verified-root> -Force` and wait for complete exit.

`.CloseMainWindow()` is preferred because it sends the normal window-close request; Microsoft documents that it can be refused and that forceful termination may lose process state. Waiting uses a bounded timeout rather than indefinite blocking.

POSIX flow:

1. Re-read PID, PPID, executable and command line.
2. Verify identity and derive the highest ancestor still inside the same app bundle/install root.
3. Reject self-descendant execution.
4. Send `SIGTERM` to the exact verified root and poll for backend exit for up to 15 seconds.
5. If still active, revalidate identity, send `SIGKILL` to the exact verified root and wait for complete exit.

The module never kills by process name and never accepts an arbitrary PID from CLI input.

## Setup Flow

1. Run discovery and print configuration/process evidence.
2. Prompt for Key, fetch models and select a model as today.
3. Ask whether local conversations should be visible under `xi_ai`.
4. For `N`, skip process control and all session inspection.
5. For `Y` with an active desktop:
   - `--dry-run`: report that the verified PID would be closed; do not signal it;
   - normal run: close and wait, then re-run desktop-process discovery;
   - abort if the target remains or respawns.
6. Validate Codex version, SQLite schema and rollout changes.
7. Build the transaction, back up, mutate and validate exactly as today.
8. Tell the user that Codex was closed and must be restarted.

No target file is written before process shutdown and all existing pre-transaction validation succeeds.

## One-Line Distribution

Keep the existing verified GitHub bootstrap and add copy-ready one-line commands to the README and release usage output.

The Windows PowerShell command performs these operations in one pasted line:

1. Use the fixed public repository `xiziqiwuyou/xi-ai-codex-configurator` and explicit `latest` release selector.
2. Create a deterministic temporary bootstrap directory.
3. Download `xi-ai-codex-bootstrap.py` and its `.sha256` file with retries.
4. Compare SHA-256 locally and stop on mismatch.
5. Run the verified bootstrap with `--repo`, `--version latest` and `--configure`.

The POSIX command performs the same operations inside one `sh -c` invocation using `curl` plus `sha256sum` or `shasum`.

The command may be long internally, but the user interaction is one paste and one Enter. It does not use `irm | iex` or `curl | sh`; the first executed program remains a local Python file whose checksum was verified against the release asset.

## Testability

- Inject the desktop closer into the CLI so tests never terminate real processes.
- Unit-test target derivation from synthetic process trees.
- Unit-test graceful success, identity mismatch, self-descendant rejection, timeout and permission failure.
- CLI tests assert closer call order, no-write failure behavior, no-close `N`/dry-run/detect-only branches, respawn rejection and Chinese status.
- Existing transaction rollback tests remain unchanged.

## Compatibility

- Existing constructors receive optional/defaulted ancestry fields where practical to limit churn.
- No new runtime dependency is added; use Python standard library plus PowerShell already required for Windows process inspection.
- `status`, `validate` and `restore` retain current behavior. Automatic close is scoped only to `setup` + `Y`.

## Risks

- Forceful termination can interrupt an active task or lose UI state. The user explicitly accepted this trade-off; the implementation limits it to an exact identity revalidation after graceful timeout.
- Running setup inside Codex cannot survive closing its own parent process, so the self-descendant guard is mandatory.
- A desktop backend can respawn; post-close rediscovery is mandatory before SQLite access and mutation.
- A one-line command can become unsafe if shortened to remote piping; checksum verification remains mandatory even at the cost of command length.
