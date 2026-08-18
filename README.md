# Xi-AI Codex Configurator

This project safely configures Codex CLI, the Codex desktop app, and the Codex
IDE extension to use Xi-AI through the Responses API.

It detects the local Codex installation, asks once for a machine-specific API
token, fetches `https://api.xi-ai.cn/v1/models`, merges those remote models with
the Codex bundled model catalog, lets the user select a default model, and
optionally makes existing local conversations visible under the `xi_ai`
provider.

## Requirements

- Python 3.11 or newer
- An installed Codex client
- A Xi-AI API token
- Close running Codex clients before choosing conversation migration

## Run

Windows PowerShell:

```powershell
.\scripts\setup.ps1
```

macOS or Linux:

```sh
sh scripts/setup.sh
```

The setup flow is interactive:

1. Codex executable, version, and `CODEX_HOME` are detected and displayed.
2. Press Enter, then enter the API token using the hidden prompt.
3. The tool fetches the Xi-AI model list.
4. Select a default model from the numbered menu.
5. Choose whether existing conversations should be visible under `xi_ai`.
6. The tool creates a complete backup and applies the validated configuration.

## Endpoint Mapping

The public service origin is fixed and cannot be overridden:

```text
Origin:        https://api.xi-ai.cn
Provider base: https://api.xi-ai.cn/v1
Models:       https://api.xi-ai.cn/v1/models
Responses:    https://api.xi-ai.cn/v1/responses
```

Codex is configured with `wire_api = "responses"`, so the provider base must
include `/v1` exactly once.

## Conversation Migration

Answering `Y` does not upload conversations or projects to Xi-AI. It creates a
backup, then updates only local provider-visibility metadata in rollout JSONL
files and `state_5.sqlite`. Session ids, messages, attachments, project paths,
source files, and timestamps remain local and are not sent anywhere.

Answering `N` leaves all session files and the session database untouched.

## Commands

Run commands directly from the repository:

```powershell
$env:PYTHONPATH = "src"
python -m codex_configurator status
python -m codex_configurator validate
python -m codex_configurator restore
```

Use `setup --dry-run` to fetch and validate models and preview changes without
writing target files.

## Backup and Restore

Backups are stored under:

```text
<CODEX_HOME>/backup-xi-ai/<timestamp>/
```

Each backup contains a secret-free manifest, SHA-256 hashes, original config
and catalog files, affected rollout files, and a consistent SQLite snapshot
when conversation migration is selected.

Restore the latest backup with:

```powershell
$env:PYTHONPATH = "src"
python -m codex_configurator restore
```

Restart Codex after setup or restore.

## Security

- The token is requested exactly once through hidden input.
- Tokens are not accepted through command-line arguments or environment
  variables.
- Tokens are never printed or stored in manifests/tests.
- The token is written only to the local Codex provider configuration.
- Xi-AI endpoint errors are reported without response headers or token values.
- Invalid model responses, TOML, JSON, or unsupported session schemas fail
  before mutation or trigger automatic rollback.

## Development

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m compileall src tests
python -m codex_configurator --help
```
