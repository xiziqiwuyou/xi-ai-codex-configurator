# Codex Configurator Contract

This specification describes the cross-platform Xi-AI Codex configurator.
It is a backend/CLI contract because it crosses terminal input, HTTP, TOML,
JSON catalog, rollout JSONL, and SQLite state.

## 1. Scope / Trigger

- Trigger: the project configures Codex to use the fixed Xi-AI Responses API.
- In scope: local Codex discovery, one-time hidden token input, model catalog
  merge, managed TOML updates, optional local conversation visibility repair,
  backup, rollback, and restore.
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
) -> int
```

Supported commands are `setup [--dry-run]`, `status`, `validate`, and
`restore [--backup PATH]`. All commands accept `--codex-home` and
`--codex-bin`; only `setup` reads interactive input.

Core storage signatures are:

```python
def merge_config(existing: str, *, model: str, catalog_path: Path, token: str) -> str
def fetch_remote_model_ids(token: str, *, url: str = MODELS_URL, opener=urlopen) -> list[str]
def update_sqlite_provider(path: Path, target_provider: str) -> int
def apply_setup(codex_home: Path, changes: SetupChanges, *, fail_at: str | None = None) -> Path
def restore_backup(codex_home: Path, backup_dir: Path) -> None
```

## 3. Contracts

### Endpoint and provider

```text
ORIGIN        = https://api.xi-ai.cn
API_BASE      = https://api.xi-ai.cn/v1
MODELS_URL    = https://api.xi-ai.cn/v1/models
RESPONSES_URL = https://api.xi-ai.cn/v1/responses
PROVIDER_ID   = xi_ai
```

The generated provider is:

```toml
model_provider = "xi_ai"
preferred_auth_method = "apikey"
forced_login_method = "api"
model_catalog_json = "<CODEX_HOME>/xi-ai-model-catalog.json"

[model_providers.xi_ai]
name = "Xi-AI"
base_url = "https://api.xi-ai.cn/v1"
wire_api = "responses"
experimental_bearer_token = "<local token>"
```

The token is accepted only from one hidden prompt. It is never read from an
environment variable or command-line argument.

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

### Backup and restore

Backups live under `<CODEX_HOME>/backup-xi-ai/<timestamp>/`. The manifest
contains provider, Codex home, relative target paths, existence flags, SHA-256
hashes, timestamps, and the session-migration flag. It never contains the API
token. SQLite backups use `VACUUM INTO` before mutation.

## 4. Validation & Error Matrix

| Condition | Required result |
| --- | --- |
| Python is missing or older than 3.11 | launcher exits before setup |
| Codex executable is missing | configure may use bundled fallback; `Y` migration is rejected |
| Empty token | exit with no target-file writes |
| HTTP 401/403 or malformed model JSON | exit with no target-file writes |
| Missing/duplicate catalog slug | exit with no target-file writes |
| Unsupported SQLite schema | reject `Y` before mutation |
| Existing SQLite WAL/SHM or exclusive-lock failure | reject `Y` and preserve sidecars |
| Any mutation phase fails | restore all affected files and SQLite snapshot |
| Restore path is outside `backup-xi-ai` | reject before writing |
| Restore manifest has invalid paths, hashes, schema, or duplicate targets | reject before writing |
| Configured model is absent from the active catalog | `validate` fails |

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
- Bad: `N` opens SQLite to inspect or rewrite it; this violates the strict
  no-session-write branch.
- Bad: using `https://api.xi-ai.cn` as `base_url` or appending `/v1` twice;
  this breaks the Responses route.
- Bad: restoring from a manifest with `../` or a backup-root target; reject it
  before any target replacement.

## 6. Tests Required

- Endpoint tests assert exact `/v1/models` and `/v1/responses` URLs and reject
  `/v1/v1`.
- Credential tests assert one Enter gate, one hidden prompt, and empty-token
  abort behavior.
- Remote-model tests assert bearer header construction, deduplication,
  malformed-shape rejection, and secret-free errors.
- Catalog/TOML tests assert bundled retention, remote append, provider-table
  replacement, and preservation of MCP/profiles/projects/unknown settings.
- Session tests assert rollout-only provider edits, SQLite visibility repair,
  preservation of non-empty `thread_source`, and WAL/SHM refusal.
- Transaction tests inject failure after config, catalog, rollout, and SQLite
  phases and assert original bytes/hashes are restored.
- CLI tests assert `N` leaves session/database bytes unchanged and `Y` without
  a detected Codex version performs no writes.
- Restore tests reject outside-root paths, corrupt hashes, duplicate targets,
  and malformed manifests before writing.
- Launcher checks include PowerShell parsing and POSIX `sh -n` when a POSIX
  shell is available.

## 7. Wrong vs Correct

### Wrong

```toml
[model_providers.xi_ai]
base_url = "https://api.xi-ai.cn"
wire_api = "responses"
```

This makes Codex resolve the wrong resource path.

### Correct

```toml
[model_providers.xi_ai]
base_url = "https://api.xi-ai.cn/v1"
wire_api = "responses"
```

This maps Codex to `https://api.xi-ai.cn/v1/responses` exactly once.

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
