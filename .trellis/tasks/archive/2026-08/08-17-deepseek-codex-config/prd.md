# Xi-AI Codex one-click configurator

## Goal

Provide a cross-platform one-click command-line workflow that configures Codex
to use the Xi-AI third-party API, prompts once for the machine-specific token,
discovers and installs the remote model list while retaining Codex's bundled
models, and optionally migrates existing local Codex conversations so they are
visible under the new provider.

## Confirmed facts

- The user-facing API origin is fixed at `https://api.xi-ai.cn`.
- A no-token `GET https://api.xi-ai.cn/v1/models` returns `401`, confirming the
  authenticated model-list route exists.
- A no-token `POST https://api.xi-ai.cn/v1/responses` returns `401`, confirming
  the authenticated Responses route exists.
- Codex `0.144.1` supports custom `model_providers`, `wire_api = "responses"`,
  `model_catalog_json`, `codex debug models --bundled`, and local session resume.
- The configured provider base must be `https://api.xi-ai.cn/v1`; Codex then
  calls the `/responses` resource through the Responses wire API.
- `model_catalog_json` supplies the active picker catalog. Local evidence shows
  the current custom catalog contains ten models while the bundled catalog
  contains eight, so the generated catalog must merge both sources rather than
  writing only remote ids.
- Codex stores provider metadata in rollout `session_meta` records and in
  `state_5.sqlite`'s `threads.model_provider` column. A provider metadata repair
  can make retained local sessions visible under another provider, but this is
  not the same as uploading old conversations to the remote API.
- API tokens are secrets and must never be committed, printed, passed as CLI
  arguments, or written to test fixtures.

## Requirements

### R1. Codex discovery

The script must detect the Codex executable and configuration home before any
prompt or write. It checks `CODEX_HOME`, platform defaults, `PATH`, common npm
locations, and supported desktop application locations. The preflight report
shows the detected executable, version, config home, and target files.

### R2. Interactive token flow

After preflight, the script displays a message asking the user to press Enter.
It then prompts exactly once for the token using hidden input. The token is not
read from an environment variable or command-line argument. An empty token
aborts without changing any file.

### R3. Responses endpoint adaptation

The public origin remains `https://api.xi-ai.cn`, but the managed Codex provider
uses the stable id `xi_ai`, `base_url = "https://api.xi-ai.cn/v1"`, and
`wire_api = "responses"`. The endpoint is not user-configurable. The script
must not produce `/v1/v1/responses` or route Codex to the website origin.

### R4. Model discovery, merge, and selection

After token input, the script requests `GET /v1/models` with bearer
authentication and validates the OpenAI-compatible `data[].id` response. It
loads the Codex bundled catalog with `codex debug models --bundled`, merges it
with every unique remote model id, writes a valid custom catalog, displays the
remote models as a numbered menu, and writes the selected id as the default
`model`. Model ids that match bundled slugs retain the bundled Codex metadata.
Unknown remote ids receive conservative text/Responses-compatible metadata.

If model retrieval or catalog validation fails, no configuration is written.
The generated catalog must keep Codex's bundled models selectable alongside the
Xi-AI models.

### R5. Configuration merge and token storage

The script updates only its managed root keys and `[model_providers.xi_ai]`.
Existing MCP servers, hooks, trust settings, sandbox settings, profiles, other
providers, and unknown TOML content must remain semantically unchanged. The
token is written only to the local provider's `experimental_bearer_token` field
and all diagnostics redact it.

### R6. Optional conversation visibility migration

After model selection, the script asks whether to merge existing conversations
using a `Y/N` prompt. `N` leaves all session files and databases untouched. The
proposed safe meaning of `Y` is a local, reversible visibility migration:

- back up every affected rollout file and the SQLite state database;
- change rollout `session_meta.payload.model_provider` to `xi_ai`;
- change `state_5.sqlite.threads.model_provider` to `xi_ai`;
- repair the visibility flags required by the current Codex schema;
- preserve rollout ordering/timestamps and existing project paths;
- never send historical prompts, responses, files, or project data to Xi-AI.

The user-facing wording must not claim that local history was uploaded to the
remote API.

### R7. Transaction, backup, and restore

Before mutation, the script creates a timestamped backup with a manifest and
hashes for `config.toml`, the generated model catalog, every rollout selected
for migration, and a consistent SQLite backup. Candidate TOML/JSON must validate
before replacement. Any failure restores every changed target. A `restore`
command reverts the latest complete backup.

### R8. Platforms and documentation

The repository provides Windows PowerShell and macOS/Linux shell launchers over
one Python 3.11+ standard-library implementation. The README documents the
interactive flow, endpoint mapping, model merge, `Y/N` conversation behavior,
backup location, rollback, security boundaries, and required Codex restart.

## Acceptance criteria

- [ ] Windows and POSIX launchers resolve the same Python CLI and fail safely
  when Python or Codex cannot be located.
- [ ] Preflight reports Codex/version/config paths before requesting a token.
- [ ] Pressing Enter leads to exactly one hidden token prompt; empty input or
  authentication failure causes zero target-file changes.
- [ ] The managed provider is `xi_ai` with base URL
  `https://api.xi-ai.cn/v1` and Responses wire API.
- [ ] A mocked Responses request resolves to `/v1/responses` exactly once.
- [ ] `/v1/models` is fetched with a redacted bearer token and invalid response
  shapes abort safely.
- [ ] The generated catalog contains every bundled Codex model plus all unique
  remote model ids, and the selected remote model becomes the default.
- [ ] Existing unrelated TOML settings survive the merge.
- [ ] Choosing `N` leaves rollout files and SQLite byte-for-byte unchanged.
- [ ] Choosing `Y` performs only the approved local visibility migration,
  creates a complete backup, preserves project paths/timestamps, and can be
  restored.
- [ ] A simulated failure at every transaction phase restores all originals.
- [ ] Tokens never appear in repository files, process arguments, stdout,
  stderr, manifests, snapshots, or tests.
- [ ] Automated tests cover path discovery, endpoint joining, prompts, model
  merge/deduplication, TOML preservation, session migration, rollback, and
  both platform launchers.

## Key decisions

- `Y` means only local Codex visibility migration. It updates Provider metadata
  in rollout files and `state_5.sqlite` after a complete backup.
- Historical prompts, responses, attachments, project paths, and source files
  are never uploaded or replayed to Xi-AI.
- `N` is a strict no-write path for session files and the session database.

## Out of scope

- Uploading or replaying historical conversation content to Xi-AI.
- Modifying project source files or moving project directories.
- Adding a local protocol proxy; Xi-AI already exposes `/v1/responses`.
- Supporting a user-defined provider origin in the MVP.
- Managing billing, account creation, or token issuance.
