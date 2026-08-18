# Improve Codex path discovery

## Goal

Make the Xi-AI configurator reliably locate Codex across Windows, macOS, and
Linux installations whose executable and configuration paths differ. Running
Codex may improve desktop-app detection, but must not be a prerequisite.

## Background

- The current implementation finds a runnable CLI through `PATH` plus a small
  set of npm and application paths.
- It resolves configuration storage separately through `--codex-home`,
  `CODEX_HOME`, or the default `~/.codex` directory.
- On this Windows machine, the npm CLI is
  `C:/Users/56252/AppData/Roaming/npm/codex.ps1`, while the running desktop
  backend is a Microsoft Store package executable under
  `C:/Program Files/WindowsApps/.../app/resources/codex.exe`.
- The desktop backend command is `codex.exe ... app-server`. Its path is
  visible through process inspection, but executing that Store path directly
  can fail with access denied.
- The desktop app and CLI share the local `~/.codex` state on the inspected
  machine, but an executable path alone does not prove a custom `CODEX_HOME`.

## Requirements

### R1. Separate discovery identities

Discovery must report the runnable CLI, detected desktop backend, Codex home,
their sources, and whether a desktop process is currently active. A desktop
backend path must never silently replace a runnable CLI path.

### R2. Runnable CLI priority

Resolve a runnable CLI in this order:

1. explicit `--codex-bin`;
2. `PATH` (`codex`, platform wrappers, and executable variants);
3. known package-manager locations such as npm, Homebrew, and user-local bins;
4. registered desktop application installation paths when executable;
5. a running desktop backend path only when it can be executed and its version
   output is recognized.

Candidates that exist but cannot run must be recorded as diagnostics and
skipped without aborting lower-priority discovery.

### R3. Desktop process detection

Windows process inspection may identify a desktop backend when the executable
is named `codex.exe` and its command line contains the `app-server` command.
Generic `ChatGPT.exe`, renderer, helper, extension-host, and processes that only
mention the word Codex must not count.

macOS/Linux may inspect processes only through safe, read-only standard-library
mechanisms; inability or permission denial must degrade to “not detected”.

### R4. Configuration-home independence

Resolve the target home independently:

1. explicit `--codex-home`;
2. `CODEX_HOME`;
3. platform default `~/.codex`.

Validate confidence using local markers such as `config.toml`,
`state_5.sqlite`, `sessions/`, and `archived_sessions/`. Process location must
not redirect configuration writes into an application installation directory.

### R5. User-facing preflight

Before requesting a token, display:

- runnable CLI path, source, and version;
- desktop backend path, source, PID, and active state when detected;
- Codex home path, source, marker count, and confidence;
- every target file/directory already shown by the existing preflight;
- warnings for non-runnable candidates or conflicting evidence.

`setup --detect-only` must stop after this report without requesting a token,
calling Xi-AI, or writing any file. Both platform launchers pass this option to
the shared Python CLI.

### R6. Session-migration safety

The `Y` conversation-visibility path requires a recognized Codex version from
a runnable CLI. If the desktop app/backend is active, migration must fail
before writing config, catalog, rollout, or SQLite state and tell the user to
close Codex. The normal `N` configuration path may proceed while the desktop
app is running, followed by a restart notice.

### R7. Compatibility

- Preserve all existing explicit CLI flags and discovery behavior.
- Keep Python 3.11+ standard-library-only runtime dependencies.
- Process inspection errors are non-fatal and secret-free.
- Tests must not inspect or modify the real user `.codex` directory.

### R8. Remote distribution and local execution

The tool must support a remote bootstrap flow for machines that do not already
contain this repository:

1. Download a version-pinned bootstrap/release manifest over HTTPS.
2. Download a complete versioned bundle containing `src/`, `assets/`, and the
   platform launchers; downloading only `setup.ps1` or `setup.sh` is invalid.
3. Verify the bundle before execution. Prefer a detached Ed25519 signature;
   SHA-256 is required at minimum for the first release.
4. Extract to a versioned local cache or temporary directory and execute from
   that local directory.
5. Run `setup --detect-only` first. This stage performs no token input, Xi-AI
   request, or local Codex write.
6. Only after the local preflight is understood, run normal `setup` and enter
   the machine-specific Key once through hidden input.

Remote distribution must never provide or overwrite the user's `config.toml`,
session database, rollout files, API Key, or conversation content. Any remote
configuration is release metadata/template input and is locally validated and
merged through the existing allowlist.

## Acceptance Criteria

- [ ] Existing `PATH`, explicit home, environment home, and default-home tests
  continue to pass.
- [ ] A mocked Windows `codex.exe ... app-server` process is reported as a
  desktop backend without replacing the runnable npm CLI.
- [ ] `ChatGPT.exe`, renderer/helper processes, and unrelated commands are
  ignored.
- [ ] An inaccessible Store backend is retained as desktop evidence but is not
  used for version/model commands.
- [ ] A runnable process-derived backend may be used only when no higher
  priority CLI exists and it passes the same version validation.
- [ ] Home discovery reports explicit/environment/default source, marker
  evidence, and deterministic confidence.
- [ ] No application installation directory is ever selected as `CODEX_HOME`
  solely from a process path.
- [ ] Preflight output clearly distinguishes CLI, desktop backend, and home.
- [ ] `setup --detect-only` works through both launchers and performs no prompt,
  network request, or target write.
- [ ] `Y` with an active desktop backend exits before any target write.
- [ ] `N` retains current behavior and leaves session files/databases untouched.
- [ ] All unit tests, compile checks, PowerShell syntax checks, and available
  POSIX checks pass.
- [ ] A remote bundle can be downloaded, verified, extracted locally, and run
  with `--detect-only` before any token or target write.
- [ ] A downloaded release cannot place a Key in a URL, command argument,
  environment variable, manifest, or remote file.

## Out of Scope

- Terminating or restarting Codex processes automatically.
- Reading another user's processes or configuration directory.
- Modifying Microsoft Store/AppX, macOS application bundles, npm, or Homebrew
  installation directories.
- Inferring an undocumented custom `CODEX_HOME` from an executable path.
- Uploading local conversation content.

## Key Decisions

- Opening Codex is optional and improves only desktop detection.
- Process detection is read-only evidence, not the primary configuration-home
  resolver.
- The script asks the user to close Codex only for the `Y` migration branch.
- Access-denied process executables are tolerated as desktop-install evidence.
- Remote release hosting, signature verification, and local cache paths are
  part of the distribution layer; they do not change the fixed Xi-AI API URL.

## GitHub Release Decision

- Distribution uses public GitHub Releases.
- The repository is supplied at runtime as `--repo OWNER/REPO` or
  `GITHUB_REPOSITORY`; no unverified owner/repo is hard-coded in the tool.
- Each release publishes `xi-ai-codex-bundle.zip`, its
  `xi-ai-codex-bundle.zip.sha256`, and the standalone
  `xi-ai-codex-bootstrap.py` downloader.
- A version is pinned with `--version TAG`; `latest` is supported only as an
  explicit convenience fallback.
