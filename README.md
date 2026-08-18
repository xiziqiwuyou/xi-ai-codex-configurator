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

Run a safe real-machine discovery test before entering a token:

```powershell
.\scripts\setup.ps1 --detect-only
```

```sh
sh scripts/setup.sh --detect-only
```

This mode performs no Xi-AI request, does not ask for a token, and does not
write configuration, catalog, session, database, or backup files.

## GitHub Releases

For a computer without this repository, publish a tagged GitHub Release. The
release workflow creates these assets:

```text
xi-ai-codex-bundle.zip
xi-ai-codex-bundle.zip.sha256
xi-ai-codex-bootstrap.py
xi-ai-codex-bootstrap.py.sha256
xi-ai-codex-release.json
```

Download and verify the standalone bootstrap first. It downloads and verifies
the complete bundle into a versioned local cache. Without `--configure`, it
always runs `--detect-only`; entering a Key requires an explicit second step.

Windows PowerShell:

```powershell
$repo = "OWNER/REPO"
$tag = "v0.2.2"
$dir = Join-Path $env:TEMP "xi-ai-codex-bootstrap"
New-Item -ItemType Directory -Force $dir | Out-Null
Invoke-WebRequest "https://github.com/$repo/releases/download/$tag/xi-ai-codex-bootstrap.py" -OutFile (Join-Path $dir "xi-ai-codex-bootstrap.py")
Invoke-WebRequest "https://github.com/$repo/releases/download/$tag/xi-ai-codex-bootstrap.py.sha256" -OutFile (Join-Path $dir "xi-ai-codex-bootstrap.py.sha256")
$expected = (Get-Content (Join-Path $dir "xi-ai-codex-bootstrap.py.sha256") -Raw).Trim().Split()[0].ToLowerInvariant()
$actual = (Get-FileHash (Join-Path $dir "xi-ai-codex-bootstrap.py") -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expected -ne $actual) { throw "Bootstrap checksum verification failed" }
py -3 (Join-Path $dir "xi-ai-codex-bootstrap.py") --repo $repo --version $tag --detect-only
py -3 (Join-Path $dir "xi-ai-codex-bootstrap.py") --repo $repo --version $tag --configure
```

macOS/Linux:

```sh
repo="OWNER/REPO"
tag="v0.2.2"
dir="${TMPDIR:-/tmp}/xi-ai-codex-bootstrap"
mkdir -p "$dir"
curl --fail --location "https://github.com/$repo/releases/download/$tag/xi-ai-codex-bootstrap.py" -o "$dir/xi-ai-codex-bootstrap.py"
curl --fail --location "https://github.com/$repo/releases/download/$tag/xi-ai-codex-bootstrap.py.sha256" -o "$dir/xi-ai-codex-bootstrap.py.sha256"
(cd "$dir" && { command -v sha256sum >/dev/null 2>&1 && sha256sum -c xi-ai-codex-bootstrap.py.sha256 || shasum -a 256 -c xi-ai-codex-bootstrap.py.sha256; })
python3 "$dir/xi-ai-codex-bootstrap.py" --repo "$repo" --version "$tag" --detect-only
python3 "$dir/xi-ai-codex-bootstrap.py" --repo "$repo" --version "$tag" --configure
```

The remote bundle never replaces the user's `config.toml`; it only supplies
verified program files and the fallback model catalog. Configuration and local
conversation metadata are merged on the target computer.

Pushing a version tag triggers the release workflow. For example:

```sh
git tag v0.2.2
git push origin v0.2.2
```

The workflow runs the full test suite, packages the five assets above, and
creates the GitHub Release. Re-running the workflow replaces assets with the
same fixed names.

The setup flow is interactive:

1. The runnable Codex CLI, running desktop backend, version, and `CODEX_HOME`
   are detected and displayed with their discovery sources.
2. Press Enter, then enter the API token using the masked prompt.
3. The tool fetches the Xi-AI model list.
4. Select a default model from the numbered menu.
5. Choose whether existing conversations should be visible under `xi_ai`.
6. The tool creates a complete backup and applies the validated configuration.

## Path Discovery

The runnable CLI, desktop application backend, and configuration home are
different identities and may live in unrelated directories.

Discovery uses this order for a runnable CLI:

1. `--codex-bin`;
2. `PATH`;
3. common npm, Homebrew, and user-local locations;
4. registered desktop installation paths;
5. a running `codex ... app-server` backend only when it can be executed and
   returns a recognized Codex version.

The configuration target is resolved independently through `--codex-home`,
`CODEX_HOME`, or `~/.codex`. The script never writes configuration into an npm,
Microsoft Store/AppX, or application-bundle installation directory merely
because a Codex executable was found there.

Opening Codex before setup is optional. When running, its `app-server` process
can help identify the desktop installation. Some Store executables can be
observed but cannot be launched directly because of operating-system access
controls; they remain desktop evidence and are not treated as the runnable
CLI.

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

If a Codex desktop backend is running, answering `Y` exits before any target
file is written. Close Codex and run the script again from an external terminal
to perform the local visibility migration. The transaction layer also checks
SQLite WAL/SHM and locking state before mutation.

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

- The token is requested exactly once through masked input.
- Each received token character is shown only as `*`, so paste is visible
  without exposing the token.
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
