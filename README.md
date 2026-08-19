# Xi-AI Codex Configurator

This project safely configures Codex CLI, the Codex desktop app, and the Codex
IDE extension to use Xi-AI through the Responses API.

It detects the local Codex installation, asks once for a machine-specific API
token, fetches `https://api.xi-ai.net/v1/models`, merges those remote models with
the Codex bundled model catalog, lets the user select a default model, and
optionally makes existing local conversations visible under the `xi_ai`
provider.

## Requirements

- Python 3.11 or newer
- An installed Codex client
- A Xi-AI API token
- Run conversation migration from a system terminal, not from inside Codex

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

## Verified HTTPS Releases

Computers without a repository clone download releases from the fixed public
source `https://download.xi-ai.net/xi-ai-codex`. Each immutable version
directory contains these assets:

```text
xi-ai-codex-bundle.zip
xi-ai-codex-bundle.zip.sha256
xi-ai-codex-bootstrap.py
xi-ai-codex-bootstrap.py.sha256
xi-ai-codex-release.json
```

The standalone bootstrap is checked against both the release manifest and its
independent checksum before Python runs. It then applies the same two checks to
the bundle before extracting it into a versioned local cache. Without
`--configure`, it always runs `--detect-only`.

### One-line setup

Windows PowerShell (paste once, then press Enter):

```powershell
& {$ErrorActionPreference='Stop';Set-StrictMode -Version 3;$b='https://download.xi-ai.net/xi-ai-codex';$d=Join-Path $env:TEMP ('xi-ai-codex-bootstrap-'+[guid]::NewGuid().ToString('N'));New-Item -ItemType Directory $d|Out-Null;if(Get-Command py -ErrorAction SilentlyContinue){$x='py';$xa=@('-3')}elseif(Get-Command python -ErrorAction SilentlyContinue){$x='python';$xa=@()}else{throw '需要 Python 3.11 或更高版本'};function Get-XiFile($u,$o,$z){curl.exe --proto '=https' --tlsv1.2 --progress-bar --fail --max-redirs 0 --retry 3 --retry-all-errors --max-filesize $z --header 'Cache-Control: no-cache' $u --output $o;if($LASTEXITCODE -ne 0){throw "下载失败：$u"}};try{$l=Join-Path $d 'latest.json';Get-XiFile "$b/latest.json" $l 1048576;$c="import json,re,sys;h=lambda p:dict(p) if len(p)==len(dict(p)) else (_ for _ in ()).throw(ValueError('duplicate key'));j=json.load(open(sys.argv[1],encoding='utf-8'),object_pairs_hook=h,parse_constant=lambda _v:(_ for _ in ()).throw(ValueError('non-finite number')));ok=set(j)=={'schema_version','version'} and type(j['schema_version']) is int and j['schema_version']==1 and isinstance(j['version'],str) and j['version']!='latest' and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',j['version']);ok or (_ for _ in ()).throw(SystemExit('invalid latest.json'));print(j['version'])";$v=& $x @xa -c $c $l;if($LASTEXITCODE -ne 0){throw 'latest.json 校验失败'};$f=Join-Path $d 'xi-ai-codex-release.json';Get-XiFile "$b/$v/xi-ai-codex-release.json" $f 1048576;$p=Join-Path $d 'xi-ai-codex-bootstrap.py';$s="$p.sha256";Get-XiFile "$b/$v/xi-ai-codex-bootstrap.py" $p 10485760;Get-XiFile "$b/$v/xi-ai-codex-bootstrap.py.sha256" $s 1048576;$c="import hashlib,json,re,sys;h=lambda p:dict(p) if len(p)==len(dict(p)) else (_ for _ in ()).throw(ValueError('duplicate key'));m=json.load(open(sys.argv[1],encoding='utf-8'),object_pairs_hook=h,parse_constant=lambda _v:(_ for _ in ()).throw(ValueError('non-finite number')));a=m.get('bootstrap');c=open(sys.argv[3],encoding='ascii').read().strip();q=re.fullmatch(r'([0-9A-Fa-f]{64})[ \t]+\*?xi-ai-codex-bootstrap\.py',c);data=open(sys.argv[2],'rb').read();ok=set(m)=={'schema_version','version','bundle','bootstrap'} and type(m.get('schema_version')) is int and m['schema_version']==1 and m.get('version')==sys.argv[4] and isinstance(a,dict) and set(a)=={'name','sha256','size'} and a['name']=='xi-ai-codex-bootstrap.py' and isinstance(a['sha256'],str) and re.fullmatch(r'[0-9a-f]{64}',a['sha256']) and type(a['size']) is int and 0<a['size']<=10485760 and a['size']==len(data) and q and q.group(1).lower()==a['sha256'] and hashlib.sha256(data).hexdigest()==a['sha256'];ok or (_ for _ in ()).throw(SystemExit('bootstrap verification failed'))";& $x @xa -c $c $f $p $s $v;if($LASTEXITCODE -ne 0){throw 'Bootstrap 校验失败'};& $x @xa $p --version $v --configure;if($LASTEXITCODE -ne 0){throw "Bootstrap 运行失败，退出码 $LASTEXITCODE"}}finally{Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue}}
```

macOS/Linux (paste once, then press Enter):

```sh
set -eu; b='https://download.xi-ai.net/xi-ai-codex'; d=$(mktemp -d "${TMPDIR:-/tmp}/xi-ai-codex-bootstrap.XXXXXX"); trap 'rm -rf "$d"' EXIT; trap 'exit 1' HUP INT TERM; get(){ curl --proto '=https' --tlsv1.2 --progress-bar --fail --max-redirs 0 --retry 3 --retry-all-errors --max-filesize "$3" -H 'Cache-Control: no-cache' "$1" -o "$2"; }; get "$b/latest.json" "$d/latest.json" 1048576; v=$(python3 -c 'import json,re,sys;h=lambda p:dict(p) if len(p)==len(dict(p)) else (_ for _ in ()).throw(ValueError("duplicate key"));j=json.load(open(sys.argv[1],encoding="utf-8"),object_pairs_hook=h,parse_constant=lambda _v:(_ for _ in ()).throw(ValueError("non-finite number")));ok=set(j)=={"schema_version","version"} and type(j["schema_version"]) is int and j["schema_version"]==1 and isinstance(j["version"],str) and j["version"]!="latest" and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",j["version"]);ok or (_ for _ in ()).throw(SystemExit("invalid latest.json"));print(j["version"])' "$d/latest.json"); get "$b/$v/xi-ai-codex-release.json" "$d/xi-ai-codex-release.json" 1048576; p="$d/xi-ai-codex-bootstrap.py"; get "$b/$v/xi-ai-codex-bootstrap.py" "$p" 10485760; get "$b/$v/xi-ai-codex-bootstrap.py.sha256" "$p.sha256" 1048576; python3 -c 'import hashlib,json,re,sys;h=lambda p:dict(p) if len(p)==len(dict(p)) else (_ for _ in ()).throw(ValueError("duplicate key"));m=json.load(open(sys.argv[1],encoding="utf-8"),object_pairs_hook=h,parse_constant=lambda _v:(_ for _ in ()).throw(ValueError("non-finite number")));a=m.get("bootstrap");c=open(sys.argv[3],encoding="ascii").read().strip();q=re.fullmatch(r"([0-9A-Fa-f]{64})[ \t]+\*?xi-ai-codex-bootstrap\.py",c);data=open(sys.argv[2],"rb").read();ok=set(m)=={"schema_version","version","bundle","bootstrap"} and type(m.get("schema_version")) is int and m["schema_version"]==1 and m.get("version")==sys.argv[4] and isinstance(a,dict) and set(a)=={"name","sha256","size"} and a["name"]=="xi-ai-codex-bootstrap.py" and isinstance(a["sha256"],str) and re.fullmatch(r"[0-9a-f]{64}",a["sha256"]) and type(a["size"]) is int and 0<a["size"]<=10485760 and a["size"]==len(data) and q and q.group(1).lower()==a["sha256"] and hashlib.sha256(data).hexdigest()==a["sha256"];ok or (_ for _ in ()).throw(SystemExit("bootstrap verification failed"))' "$d/xi-ai-codex-release.json" "$p" "$p.sha256" "$v"; python3 "$p" --version "$v" --configure
```

Both commands read `https://download.xi-ai.net/xi-ai-codex/latest.json`, pin its
validated version, and verify the local bootstrap before running it. They do
not use FTP credentials, follow cross-host redirects, or pipe downloaded code
directly into a shell.

The bootstrap defaults to `--version latest`. Use `--version <tag>` to pin a
known release, add `--refresh` to rebuild that version's local cache, and omit
`--configure` (or pass `--detect-only`) for a non-interactive safety check.

### Manual pinned-version setup

Windows PowerShell:

```powershell
$base = "https://download.xi-ai.net/xi-ai-codex"
$v = "v0.5.1"
$dir = Join-Path $env:TEMP "xi-ai-codex-bootstrap"
New-Item -ItemType Directory -Force $dir | Out-Null
curl.exe --proto '=https' --tlsv1.2 --fail --max-redirs 0 "$base/$v/xi-ai-codex-release.json" --output (Join-Path $dir "xi-ai-codex-release.json")
curl.exe --proto '=https' --tlsv1.2 --fail --max-redirs 0 "$base/$v/xi-ai-codex-bootstrap.py" --output (Join-Path $dir "xi-ai-codex-bootstrap.py")
curl.exe --proto '=https' --tlsv1.2 --fail --max-redirs 0 "$base/$v/xi-ai-codex-bootstrap.py.sha256" --output (Join-Path $dir "xi-ai-codex-bootstrap.py.sha256")
$manifest = Get-Content (Join-Path $dir "xi-ai-codex-release.json") -Raw | ConvertFrom-Json
$expected = (Get-Content (Join-Path $dir "xi-ai-codex-bootstrap.py.sha256") -Raw).Trim().Split()[0].ToLowerInvariant()
$actual = (Get-FileHash (Join-Path $dir "xi-ai-codex-bootstrap.py") -Algorithm SHA256).Hash.ToLowerInvariant()
if ($manifest.version -cne $v -or $manifest.bootstrap.name -cne "xi-ai-codex-bootstrap.py" -or $expected -cne $manifest.bootstrap.sha256 -or $actual -cne $manifest.bootstrap.sha256 -or (Get-Item (Join-Path $dir "xi-ai-codex-bootstrap.py")).Length -ne [long]$manifest.bootstrap.size) { throw "Bootstrap verification failed" }
py -3 (Join-Path $dir "xi-ai-codex-bootstrap.py") --version $v --detect-only
py -3 (Join-Path $dir "xi-ai-codex-bootstrap.py") --version $v --configure
```

macOS/Linux:

```sh
base="https://download.xi-ai.net/xi-ai-codex"
v="v0.5.1"
dir="${TMPDIR:-/tmp}/xi-ai-codex-bootstrap"
mkdir -p "$dir"
curl --proto '=https' --tlsv1.2 --fail --max-redirs 0 "$base/$v/xi-ai-codex-release.json" -o "$dir/xi-ai-codex-release.json"
curl --proto '=https' --tlsv1.2 --fail --max-redirs 0 "$base/$v/xi-ai-codex-bootstrap.py" -o "$dir/xi-ai-codex-bootstrap.py"
curl --proto '=https' --tlsv1.2 --fail --max-redirs 0 "$base/$v/xi-ai-codex-bootstrap.py.sha256" -o "$dir/xi-ai-codex-bootstrap.py.sha256"
python3 -c 'import hashlib,json,sys;m=json.load(open(sys.argv[1],encoding="utf-8"));data=open(sys.argv[2],"rb").read();expected=open(sys.argv[3],encoding="ascii").read().split()[0].lower();a=m["bootstrap"];assert m["version"]==sys.argv[4] and a["name"]=="xi-ai-codex-bootstrap.py" and expected==a["sha256"]==hashlib.sha256(data).hexdigest() and a["size"]==len(data)' "$dir/xi-ai-codex-release.json" "$dir/xi-ai-codex-bootstrap.py" "$dir/xi-ai-codex-bootstrap.py.sha256" "$v"
python3 "$dir/xi-ai-codex-bootstrap.py" --version "$v" --detect-only
python3 "$dir/xi-ai-codex-bootstrap.py" --version "$v" --configure
```

The remote bundle never replaces the user's `config.toml`; it only supplies
verified program files and the fallback model catalog. Configuration and local
conversation metadata are merged on the target computer.

Pushing a version tag triggers the release workflow. For example:

```sh
git tag v0.5.1
git push origin v0.5.1
```

The workflow runs the full test suite, packages the five assets above, and uses
explicit TLS in passive mode to upload them to
`https://download.xi-ai.net/xi-ai-codex/<tag>/`. Version directories are
immutable: re-running the same tag fails instead of replacing files. After all
five exact HTTPS paths are readable and byte-identical, the workflow uploads a
temporary pointer and atomically renames it to `latest.json`. GitHub stores the
source and tag history; it is not the client download source.

The setup flow is interactive:

1. The runnable Codex CLI, running desktop backend, version, and `CODEX_HOME`
   are detected and displayed with their discovery sources.
2. Press Enter, then enter the API token using the masked prompt.
3. The tool fetches the Xi-AI model list.
4. Select a default model from the numbered menu.
5. If the model is Sol, Terra, or Luna, choose whether to preserve the current
   context setting, enable 500K, enable 1M, or restore the Codex default.
   500K writes `model_context_window = 500000` and
   `model_auto_compact_token_limit = 450000`; 1M writes `1000000` and
   `900000`. Pressing Enter preserves existing values.
6. Choose whether existing conversations should be visible under `xi_ai`.
7. If `Y` is selected, the tool closes the exact detected Codex desktop
   instance. It requests a normal exit for 15 seconds, then revalidates and
   force-stops only that instance if necessary.
8. The tool estimates backup space, shows scan/backup/migration progress,
   creates a compact rollback backup, and applies the validated configuration.
   Large batches are throttled so the terminal remains readable.

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
can help identify the desktop installation and gives the setup flow an exact
process target if conversation migration is selected. Some Store executables
can be observed but cannot be launched directly because of operating-system
access controls; they remain desktop evidence and are not treated as the
runnable CLI or configuration home.

## Endpoint Mapping

The public service origin is fixed and cannot be overridden:

```text
Origin:        https://api.xi-ai.net
Provider base: https://api.xi-ai.net/v1
Models:       https://api.xi-ai.net/v1/models
Responses:    https://api.xi-ai.net/v1/responses
```

Codex is configured with `wire_api = "responses"`, so the provider base must
include `/v1` exactly once.

### Progress feedback

The release bootstrap reports latest-pointer and manifest downloads, checksum
downloads, bundle download, SHA-256 verification, extraction, and cache
installation. A known response length shows a percentage; an unknown length
shows downloaded bytes. The one-line `curl` commands use `--progress-bar` for
the initial downloads.

When conversation migration is enabled, the configurator reports session
scanning, file/database backup, rollout updates, SQLite metadata updates, and
rollback if needed. These messages contain counts and generic stages only; no
token, conversation text, or individual session path is printed.

## Conversation Migration

Answering `Y` does not upload conversations or projects to Xi-AI. It creates a
backup, then updates only local provider-visibility metadata in rollout JSONL
files and `state_5.sqlite`. Session ids, messages, attachments, project paths,
source files, and timestamps remain local and are not sent anywhere.

Answering `N` leaves all session files and the session database untouched.

Long-context settings are independent of conversation migration. They are
written as top-level keys in `~/.codex/config.toml`, take effect after Codex is
restarted, and should be used with a new task. The configured window is an
upper bound; actual requests may use less. Cost and eligibility rules for
500K/1M are controlled by the current service provider.

If a Codex desktop backend is running, answering `Y` authorizes the script to
close that exact verified desktop instance. It first requests an orderly exit
and waits 15 seconds. If the backend remains, the script revalidates its PID,
executable, command line, and process ancestry before force-stopping the exact
GUI root and backend PIDs. It never stops processes by name.

Run setup from a system PowerShell/Terminal. If setup is running inside the
detected Codex process tree, it aborts before signaling or writing so it cannot
terminate itself halfway through migration. After shutdown, the script checks
for a respawned backend twice: before session inspection and immediately before
the transaction. A retained SQLite WAL/SHM pair is not treated as process
ownership by itself. After both process checks pass, SQLite performs a RESTART
checkpoint, integrity check and write-lock probe before creating the backup.
Never delete WAL/SHM manually because the WAL can contain committed data that
has not yet reached the main database file.

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

Each new backup contains a secret-free v2 manifest, complete config/catalog
files, a consistent SQLite snapshot when conversation migration is selected,
and compact patches containing only the original first line of each changed
rollout file. Historical session messages are not duplicated. Existing v1
full-file backups remain restorable.

If the Codex home volume does not have enough space, setup stops before writing
and asks for a backup directory on another volume. You can provide one
up-front:

```powershell
$env:PYTHONPATH = "src"
python -m codex_configurator setup --backup-root D:\Xi-AI-Backups
```

Restore the latest backup with:

```powershell
$env:PYTHONPATH = "src"
python -m codex_configurator restore --backup-root D:\Xi-AI-Backups
```

To restore one exact backup directory, use `--backup PATH`. An empty response
to the low-space prompt cancels setup; the tool never skips the backup and
continues with irreversible writes.

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
- Release clients connect only to the fixed HTTPS download host; FTPS exists
  only inside the publishing workflow and no upload credential is shipped.
- Invalid model responses, TOML, JSON, or unsupported session schemas fail
  before mutation or trigger automatic rollback.

## Development

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m compileall src tests
python -m codex_configurator --help
```
