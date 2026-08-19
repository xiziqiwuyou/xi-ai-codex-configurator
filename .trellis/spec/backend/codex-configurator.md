# Codex Configurator Contract

This specification describes the cross-platform Xi-AI Codex configurator.
It is a backend/CLI contract because it crosses terminal input, HTTP, TOML,
JSON catalog, rollout JSONL, and SQLite state.

## 1. Scope / Trigger

- Trigger: the project configures Codex to use the fixed Xi-AI Responses API.
- In scope: local Codex discovery, one-time masked token input, Simplified
  Chinese console messages, exact desktop-process shutdown, model catalog
  merge, managed TOML updates, optional local conversation visibility repair,
  verified one-line release setup, backup, rollback, and restore.
- Out of scope: uploading or replaying historical prompts, responses,
  attachments, project paths, or source files to Xi-AI.

## 2. Signatures

The public Python entry point is:

```python
def main(
    argv: list[str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
    opener: Callable = urlopen,
    output: Callable[[str], None] = print,
    desktop_closer: Callable | None = None,
    process_detector: Callable | None = None,
) -> int
```

Supported commands are `setup [--dry-run] [--detect-only]`, `status`,
`validate`, and `restore [--backup PATH]`. All commands accept `--codex-home`
and `--codex-bin`; only a normal `setup` reads interactive input.

Discovery signatures are:

```python
def discover(...) -> DiscoveryResult
def resolve_codex_home_details(...) -> tuple[Path, str, tuple[str, ...], str]
def inspect_process_records(...) -> tuple[ProcessRecord, ...]
def discover_running_codex_processes(...) -> tuple[tuple[DesktopProcess, ...], tuple[str, ...]]
def close_codex_desktop(process: DesktopProcess, ...) -> DesktopCloseResult
```

`DiscoveryResult.executable` is a runnable CLI candidate. A desktop
`app-server` process is reported separately as `desktop_process`, including
its parent PID and exact GUI root PID/executable when those can be proven from
the same application install tree. It is never implicitly treated as a
configuration directory.

Core storage signatures are:

```python
def merge_config(
    existing: str,
    *,
    model: str,
    catalog_path: Path,
    token: str,
    context: ContextConfig = PRESERVE_CONTEXT,
) -> str
def fetch_remote_model_ids(token: str, *, url: str = MODELS_URL, opener=urlopen) -> list[str]
def collect_rollout_changes(codex_home: Path, target_provider: str, *, progress=None) -> list[RolloutChange]
def update_sqlite_provider(path: Path, target_provider: str) -> int
def ensure_sqlite_ready(path: Path, *, allow_wal_recovery: bool = False) -> None
def apply_setup(
    codex_home: Path,
    changes: SetupChanges,
    *,
    fail_at: str | None = None,
    allow_wal_recovery: bool = False,
    progress=None,
) -> Path
def restore_backup(codex_home: Path, backup_dir: Path) -> None
```

## 3. Contracts

### Endpoint and provider

```text
ORIGIN        = https://api.xi-ai.net
API_BASE      = https://api.xi-ai.net/v1
MODELS_URL    = https://api.xi-ai.net/v1/models
RESPONSES_URL = https://api.xi-ai.net/v1/responses
PROVIDER_ID   = xi_ai
```

The generated provider is:

```toml
model_provider = "xi_ai"
forced_login_method = "api"
model_catalog_json = "<CODEX_HOME>/xi-ai-model-catalog.json"

[model_providers.xi_ai]
name = "Xi-AI"
base_url = "https://api.xi-ai.net/v1"
wire_api = "responses"
experimental_bearer_token = "<local token>"
```

The token is accepted only from one masked prompt. Each received character is
echoed as `*`; backspace removes the last mask character. The token itself is
never echoed, read from an environment variable, or accepted as a command-line
argument.

`preferred_auth_method` is a legacy managed key: setup removes an existing
root assignment but does not write it back because Codex CLI `0.144.1` rejects
it under `app-server --strict-config`. API-key authentication is carried by the
provider's local `experimental_bearer_token`; `forced_login_method = "api"`
remains a supported root setting.

For `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`, setup may manage two
optional top-level Codex settings. The default action preserves existing
values; the explicit presets are:

```toml
# 500K preset
model_context_window = 500000
model_auto_compact_token_limit = 450000

# 1M preset
model_context_window = 1000000
model_auto_compact_token_limit = 900000
```

“Restore Codex default” removes both keys. Other model slugs do not prompt for
these settings and preserve them. The configurator does not change reasoning
effort. Context and billing eligibility are provider-controlled; the tool does
not claim that a token is entitled to either preset.

Progress callbacks are observational only. They may report stage, state
(`start`, `update`, `complete`), current count, and total count, but must never
contain tokens, authorization headers, response bodies, conversation text, or
individual session paths. The `N` migration branch must not create or invoke a
session progress producer.

`setup --detect-only` prints discovery information and returns without reading
the token, calling Xi-AI, or writing configuration, catalog, rollout, SQLite,
or backup files. The standalone HTTPS bootstrap defaults to this mode; normal
configuration requires its explicit `--configure` switch.

### Discovery boundary

The target home resolution order is `--codex-home`, `CODEX_HOME`, then
`~/.codex`. The executable resolution order is explicit path, `PATH`, package
manager/user-local locations, registered desktop installation, then a running
`codex ... app-server` path only after version validation. Home marker evidence
(`config.toml`, `state_5.sqlite`, `sessions/`, `archived_sessions/`) is shown
with a confidence label. An inaccessible Store/AppX executable is diagnostics,
not a runnable CLI. Windows records include PPID and the `ChatGPT.exe` parent
used by the Store Codex package; POSIX records include PPID from `ps`.

### Desktop shutdown

Automatic shutdown is scoped to normal `setup` after the user selects `Y`.
`N`, `--detect-only`, and `--dry-run` never signal a process. The shutdown
target is derived from the detected `app-server` PID and parent chain, and
must stay inside the same application install root. Process names are never a
termination selector.

Before signaling, the controller revalidates backend PID, executable, command
line, root PID/executable, and caller ancestry. If setup is a descendant of the
target backend, it aborts and requires a system PowerShell/Terminal. Windows
uses `CloseMainWindow`; POSIX uses `SIGTERM`. After 15 seconds the controller
revalidates identity, then force-stops only the exact root/backend PIDs using
`Stop-Process -Id ... -Force` or `SIGKILL`. It waits up to 10 more seconds.

After shutdown, setup runs fresh desktop discovery. Inspection failure,
identity drift, permission failure, a remaining/respawned backend, or a force
timeout aborts before any target file write. SQLite readiness checks remain a
second gate before backup and mutation. Snapshot and parser failures are
normalized to `DesktopControlError`. On POSIX, `ProcessLookupError` after the
verified root is killed is benign only when the final snapshot confirms that
the backend has exited.

### FTPS publishing and HTTPS release assets

Public releases use exact asset names `xi-ai-codex-bundle.zip`,
`xi-ai-codex-bundle.zip.sha256`, `xi-ai-codex-bootstrap.py`,
`xi-ai-codex-bootstrap.py.sha256`, and `xi-ai-codex-release.json`. GitHub tags
trigger publishing, but GitHub Release API is not a client or artifact source.
Actions uploads over explicit TLS/passive FTPS to a staging directory, verifies
all five files through their public HTTPS URLs, then atomically renames staging
to the immutable `/xi-ai-codex/<tag>/` directory. Only after that it uploads a
temporary `latest.json` and renames it last. Repeating an existing tag fails.

The standalone bootstrap trusts only `https://download.xi-ai.net/xi-ai-codex`.
`latest` reads the exact `latest.json` pointer, then the matching version
manifest; an explicit tag reads only that version manifest. It constructs asset
URLs itself and rejects other hosts, ports, queries, fragments, redirects,
unsafe tags, and unknown asset names. It validates manifest schema, asset names,
sizes and SHA-256, then validates each independent checksum file, rejects unsafe
or colliding ZIP entries, extracts to a versioned cache, and runs the local
bundle. Transient URL/OS connection failures are retried three times with
bounded exponential backoff; HTTP and validation errors fail immediately.

The release manifest schema is:

```json
{
  "schema_version": 1,
  "version": "v0.3.1",
  "bundle": {
    "name": "xi-ai-codex-bundle.zip",
    "sha256": "<64 lowercase hex characters>",
    "size": 12345
  },
  "bootstrap": {
    "name": "xi-ai-codex-bootstrap.py",
    "sha256": "<64 lowercase hex characters>",
    "size": 12345
  }
}
```

The bootstrap does not accept or infer a GitHub repository. A release tag may be
supplied with `--version TAG` or `XI_AI_CODEX_VERSION`; the default is the
validated `latest.json` pointer. README one-line commands hard-code the public
HTTPS source, validate the pointer, manifest, bootstrap checksum and size, then
run the local bootstrap with the resolved tag and `--configure`. They must not
pipe downloaded script text into PowerShell or a POSIX shell, and must never
contain FTPS credentials.

### Model response and catalog

The model endpoint must return an object with a `data` array containing
non-empty string `id` fields. IDs are deduplicated in server order. The final
catalog preserves every bundled entry and appends remote-only IDs using a
conservative text/Responses template. The selected ID must be a catalog slug.

### Session visibility migration

When the user answers `Y`, only these local fields may change:

- rollout first record: `session_meta.payload.model_provider = "xi_ai"`;
- SQLite `threads.model_provider`;
- `has_user_event` is set when `first_user_message` is non-empty;
- empty `thread_source` is set to `"user"` when `first_user_message` exists.

Existing non-empty `thread_source`, event payloads, IDs, titles, cwd/project
paths, attachments, messages, and file timestamps remain unchanged. `N` must
not open the session database or rewrite rollout files.

When `Y` is selected with an active desktop, successful shutdown and fresh
no-backend discovery are required in the same setup run before session
inspection and migration continue. Normal `setup + Y` always performs fresh
discovery before session inspection, even when initial discovery found no
desktop, and repeats discovery immediately before applying the transaction.

WAL/SHM existence is database-state evidence, not proof of an active process.
Only after both process gates pass may the CLI authorize retained-WAL recovery.
SQLite then performs a bounded `wal_checkpoint(RESTART)`, requires
`PRAGMA quick_check` to return `ok`, and verifies a `BEGIN IMMEDIATE` write
transaction before backup. Direct transaction callers and restore keep the
default sidecar rejection behavior. Code must never delete or separate a WAL
from its main database because committed pages may exist only in the WAL.

### Backup and restore

Backups live under `<CODEX_HOME>/backup-xi-ai/<timestamp>/`. The manifest
contains provider, Codex home, relative target paths, existence flags, SHA-256
hashes, timestamps, and the session-migration flag. It never contains the API
token. SQLite backups use `VACUUM INTO` before mutation.

## 4. Validation & Error Matrix

| Condition | Required result |
| --- | --- |
| Python is missing or older than 3.11 | launcher exits before setup |
| Bootstrap runs under Python older than 3.11 | reject before any HTTPS request |
| Codex executable is missing | configure may use bundled fallback; `Y` migration is rejected |
| Empty token | exit with no target-file writes |
| HTTP 401/403 or malformed model JSON | exit with no target-file writes |
| Missing/duplicate catalog slug | exit with no target-file writes |
| Unsupported SQLite schema | reject `Y` before mutation |
| Existing SQLite WAL/SHM without explicit process-verified recovery authorization | reject and preserve sidecars |
| Authorized retained WAL with successful RESTART checkpoint, quick check and write lock | continue to `VACUUM INTO` backup |
| RESTART checkpoint is busy, quick check fails, or write lock cannot be acquired | reject before target business-data writes |
| Any mutation phase fails | restore all affected files and SQLite snapshot |
| Restore path is outside `backup-xi-ai` | reject before writing |
| Restore manifest has invalid paths, hashes, schema, or duplicate targets | reject before writing |
| Configured model is absent from the active catalog | `validate` fails |
| `setup --detect-only` is selected | no prompt, network call, or target write |
| Desktop `app-server` is active and user selects `Y` | close exact verified instance, rediscover, then migrate in the same run |
| Setup process is a descendant of the target Codex backend | reject before signaling or writing; require an external system terminal |
| Graceful close exceeds 15 seconds | revalidate identity, force exact root/backend PIDs, and wait up to 10 seconds |
| PID/path/command/root identity changes before force | reject before force or target-file writes |
| Desktop inspection fails or a backend remains/respawns after close | reject before target-file writes |
| Release bundle is missing or checksum mismatches | reject before extraction or local execution |
| Release metadata contains an unknown host, asset, path, redirect, size, or hash | reject before extraction or setup |
| `latest.json` is missing, malformed, or points to an unsafe version | reject before version manifest download |
| Bootstrap has no `--version` or `XI_AI_CODEX_VERSION` | resolve the fixed HTTPS `latest.json` pointer |
| Bootstrap runs without `--configure` | forward `--detect-only` and never prompt for Key |

User-facing errors must not include authorization headers, token values, or
raw response bodies.

## 5. Good / Base / Bad Cases

- Good: a valid token returns `gpt-5.6-sol` and `xi-model`; the generated
  catalog contains the eight bundled models plus `xi-model`, and the selected
  model is written as `model`.
- Base: no existing config, sessions, or SQLite database; setup writes only
  `config.toml`, the generated catalog, and a manifest.
- Good: `Y` changes provider metadata in local rollout/SQLite records while
  preserving all message content and timestamps.
- Good: Codex is absent but a valid WAL/SHM pair remains; SQLite checkpoints
  and validates it, then `VACUUM INTO` preserves committed WAL content.
- Bad: `N` opens SQLite to inspect or rewrite it; this violates the strict
  no-session-write branch.
- Bad: using `https://api.xi-ai.net` without `/v1` as `base_url` or appending `/v1` twice;
  this breaks the Responses route.
- Bad: restoring from a manifest with `../` or a backup-root target; reject it
  before any target replacement.
- Good: the npm CLI is runnable while the Store `app-server` is active; report
  both paths and use the npm CLI for version/model commands.
- Good: the Store `app-server` has a same-install-tree `ChatGPT.exe` parent;
  close that exact root, not every `ChatGPT.exe` process.
- Bad: setup runs inside the target backend and attempts to close its own
  ancestor; reject before sending a close request.
- Bad: deleting WAL/SHM to bypass readiness; committed transactions can be
  lost when the WAL has not been checkpointed into the main database.
- Bad: a force branch uses `Stop-Process -Name` or sends a signal without a
  fresh identity check.
- Bad: deriving `CODEX_HOME` from `C:\\Program Files\\WindowsApps\\...`; the
  application install directory must never become the config target.

## 6. Tests Required

- Endpoint tests assert exact `/v1/models` and `/v1/responses` URLs and reject
  `/v1/v1`.
- Credential tests assert one Enter gate, one masked prompt, and empty-token
  abort behavior.
- CLI tests assert Chinese preflight, model-selection, dry-run, success, and
  active-desktop close/migration guidance without exposing the token.
- Desktop-control tests assert same-install-tree root derivation, exact-PID
  graceful/force actions, self-descendant rejection, identity drift refusal,
  orphaned-backend handling, snapshot-error normalization, child-exit races,
  and bounded timeout failure.
- Remote-model tests assert bearer header construction, deduplication,
  malformed-shape rejection, and secret-free errors.
- Catalog/TOML tests assert bundled retention, remote append, provider-table
  replacement, and preservation of MCP/profiles/projects/unknown settings.
- Session tests assert rollout-only provider edits, SQLite visibility repair,
  preservation of non-empty `thread_source`, conservative default sidecar
  refusal, authorized retained-WAL recovery, busy-reader rejection, and backup
  preservation of committed WAL data.
- Transaction tests inject failure after config, catalog, rollout, and SQLite
  phases and assert original bytes/hashes are restored.
- CLI tests assert `N` leaves session/database bytes unchanged and `Y` without
  a detected Codex version performs no writes. Migration tests assert two fresh
  process checks and explicit recovery authorization at the transaction boundary.
- Restore tests reject outside-root paths, corrupt hashes, duplicate targets,
  and malformed manifests before writing.
- Launcher checks include PowerShell parsing and POSIX `sh -n` when a POSIX
  shell is available.
- Discovery tests assert CLI/process separation, candidate fall-through,
  AppX JSON parsing, PPID/root derivation, marker confidence, process filtering,
  and the no-write `--detect-only` mode.
- Bootstrap tests assert fixed HTTPS URL construction, latest pointer and
  manifest parsing, host/path/redirect rejection, independent checksum and
  manifest failures, transient connection retries, ZIP traversal/symlink
  rejection, cache extraction, setup-argument forwarding, known/unknown-length
  progress, retry reset, and TTY/non-TTY rendering.
- Context tests assert preserve, 500K, 1M, clear, strict integer TOML values,
  supported-model-only prompting, and no context prompt for other models.
- Session/transaction tests assert progress ordering, throttling, path-free
  output, rollback reporting, and no progress events on the `N` branch.
- Documentation tests assert copy-ready one-line commands include checksum
  verification, explicit `latest --configure`, and no remote pipe execution.

## 7. Wrong vs Correct

### Wrong

```toml
[model_providers.xi_ai]
base_url = "https://api.xi-ai.net"
wire_api = "responses"
```

This makes Codex resolve the wrong resource path.

### Correct

```toml
[model_providers.xi_ai]
base_url = "https://api.xi-ai.net/v1"
wire_api = "responses"
```

This maps Codex to `https://api.xi-ai.net/v1/responses` exactly once.

### Wrong

```sh
curl https://host/setup.sh | sh
```

This executes mutable remote code before release verification.

### Correct

```sh
curl --fail https://download.xi-ai.net/xi-ai-codex/v1.0.0/xi-ai-codex-bootstrap.py -o bootstrap.py
curl --fail https://download.xi-ai.net/xi-ai-codex/v1.0.0/xi-ai-codex-bootstrap.py.sha256 -o bootstrap.py.sha256
sha256sum -c bootstrap.py.sha256
python3 bootstrap.py --version v1.0.0 --detect-only
```

The local checksum is verified before Python executes. The bootstrap then
verifies the version manifest, its own manifest entry, and the versioned bundle
checksum; it does not receive a Key in the download command.

### Wrong

```python
requests.post(remote_url, json=historical_messages)
```

The configurator must never transmit local conversation content.

### Correct

```python
update_sqlite_provider(Path("state_5.sqlite"), "xi_ai")
```

The `Y` branch performs only local metadata repair after a complete backup.

### Wrong

```python
Path(f"{database}-wal").unlink(missing_ok=True)
```

Deleting a retained WAL can discard committed pages that are not yet present in
the main database.

### Correct

```python
ensure_sqlite_ready(database, allow_wal_recovery=True)
```

This capability is used only after fresh Codex process verification and lets
SQLite checkpoint and validate its own persistent state.

### Wrong

```python
home = process.executable.parent
```

An executable parent is an installation directory, not evidence of the active
Codex configuration home.

### Correct

```python
home = resolve_codex_home_details(explicit, env=env, home=Path.home())[0]
```

The configuration home follows the explicit/environment/default contract and
is validated independently from executable discovery.
