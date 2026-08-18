# Technical design

## User flow

```text
launch setup.ps1 / setup.sh
  -> detect Codex executable, version, and CODEX_HOME
  -> show paths and press-Enter prompt
  -> hidden token prompt (exactly once)
  -> GET https://api.xi-ai.cn/v1/models
  -> load `codex debug models --bundled`
  -> merge/deduplicate model catalogs
  -> show numbered remote-model picker
  -> ask `Merge existing conversations? [y/N]`
  -> show redacted change summary
  -> backup, validate, and atomically apply
  -> run post-write validation and restart notice
```

## Package boundaries

```text
src/codex_configurator/
  cli.py              command parsing and terminal flow
  discovery.py        Codex executable/config-home resolution
  endpoints.py        fixed origin and safe `/v1` joining
  credentials.py      Enter gate and hidden token prompt
  remote_models.py    authenticated `/v1/models` retrieval
  catalog.py          bundled + remote catalog merge
  toml_merge.py       managed-key configuration merge
  sessions.py         optional local visibility migration
  transaction.py      backup, manifest, hashes, restore
  validation.py       TOML/JSON/schema/post-write checks
  redaction.py        secret-safe messages and exceptions
```

PowerShell and POSIX launchers only discover Python and execute the same module.

## Codex discovery

Resolve the config home in this order:

1. explicit `CODEX_HOME`;
2. platform default user `.codex` directory;
3. config home reported by a supported local installation when available.

Resolve the executable using `PATH`, npm global locations, and supported Codex
desktop bundle locations. Run `codex --version` and require a supported semantic
version before enabling session migration. If the executable is missing, the
tool may configure an existing config home using the checked-in fallback bundled
catalog, but it must warn that live bundled-model extraction is unavailable.

## Endpoint contract

Constants:

```text
ORIGIN       = https://api.xi-ai.cn
API_BASE     = https://api.xi-ai.cn/v1
MODELS_URL   = https://api.xi-ai.cn/v1/models
RESPONSES_URL= https://api.xi-ai.cn/v1/responses
PROVIDER_ID  = xi_ai
```

The endpoint builder strips only a trailing slash from the fixed origin and
appends `/v1` once. It never accepts user input and has tests preventing
`/v1/v1` and website-origin requests.

Managed provider configuration:

```toml
model_provider = "xi_ai"

[model_providers.xi_ai]
name = "Xi-AI"
base_url = "https://api.xi-ai.cn/v1"
wire_api = "responses"
experimental_bearer_token = "<local token>"
```

## Credential and remote model flow

The setup command first waits for a plain Enter confirmation, then invokes
`getpass.getpass` once. It does not use environment or argument token sources.
The token is used to call `/v1/models` through `urllib.request` with an
`Authorization: Bearer` header. Errors are normalized without echoing request
headers or response content that could contain a secret.

The expected response is an object containing a `data` array of objects with
non-empty string `id` fields. Duplicate ids are removed while preserving server
order. A numbered picker requires the user to select one remote id.

## Catalog merge

The active custom catalog must retain bundled models because
`model_catalog_json` supplies the active picker catalog.

1. Run `codex debug models --bundled` and validate its JSON.
2. Fall back to a checked-in catalog snapshot only when the command is
   unavailable.
3. Preserve every bundled model entry exactly.
4. For a remote id matching a bundled slug, reuse the bundled entry.
5. For an unknown remote id, generate a conservative text-only entry from a
   versioned generic Responses template with safe context and tool defaults.
6. Append remote-only entries after bundled entries and deduplicate by slug.
7. Validate the final catalog and write it to
   `<CODEX_HOME>/xi-ai-model-catalog.json`.
8. Set `model_catalog_json` to that file and `model` to the selected id.

The CLI labels generated metadata as conservative because `/v1/models` does not
publish Codex-specific context/tool capability metadata.

## TOML merge

The merger owns these root keys:

- `model`
- `model_provider`
- `preferred_auth_method`
- `forced_login_method`
- `model_catalog_json`

It owns only `[model_providers.xi_ai]`. It updates existing managed keys or
inserts them before the first table, replaces an existing Xi-AI provider table,
and preserves all unrelated source text. The candidate must parse with
`tomllib` before backup or replacement.

## Conversation migration

The `Y` path is the approved local visibility migration. It never uploads
historical content.

Preconditions:

- supported Codex version and recognized schema;
- no active Codex process or an exclusive database lock can be acquired;
- session and database paths resolve inside the selected config home.

Operation:

1. Scan active and archived rollout JSONL files.
2. Identify the first `session_meta` record and calculate provider changes.
3. Inspect `state_5.sqlite` for the expected `threads` table/columns.
4. Create a full affected-file backup and SQLite `VACUUM INTO` snapshot.
5. Atomically replace changed rollout files while preserving their effective
   timestamps/order.
6. In one SQLite transaction, update `model_provider = 'xi_ai'`; when supported,
   repair `has_user_event` and `thread_source` for sessions with a first user
   message.
7. Leave ids, titles, cwd/project paths, messages, attachments, models, and all
   event payloads unchanged.

The `N` path does not open the database for writing and does not rewrite rollout
files.

## Backup and transaction

Use `<CODEX_HOME>/backup-xi-ai/<timestamp>/` with a manifest containing the
Codex version, provider, file paths relative to the config home, original hashes,
and operation type. Never store the token in the manifest.

Configuration/catalog replacement uses sibling temporary files plus
`os.replace`. Session migration uses temporary rollout files and a SQLite
transaction after a consistent backup. If any phase fails, restore all changed
files and the SQLite snapshot, then verify hashes before reporting failure.

## Commands

```text
setup [--dry-run]
status
validate
restore [--backup <path>]
```

`setup` owns the interactive token/model/merge flow. The other commands never
prompt for a token. `--dry-run` may use a placeholder token and fixture model
response in tests, but the real interactive setup validates the supplied token
before presenting models.
