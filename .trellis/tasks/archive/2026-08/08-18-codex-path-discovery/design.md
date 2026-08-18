# Technical design

## Discovery model

Replace the minimal three-field result with explicit, backward-compatible
discovery metadata:

```python
@dataclass(frozen=True)
class DesktopProcess:
    pid: int
    executable: Path
    command_line: str
    source: str

@dataclass(frozen=True)
class DiscoveryResult:
    codex_home: Path
    executable: Path | None
    version: str | None
    executable_source: str = "not-found"
    codex_home_source: str = "default"
    home_markers: tuple[str, ...] = ()
    home_confidence: str = "low"
    desktop_process: DesktopProcess | None = None
    warnings: tuple[str, ...] = ()
```

Defaults preserve existing tests and injected `DiscoveryResult(home, None,
None)` fixtures.

## Candidate pipeline

Each candidate carries a path and source. Discovery deduplicates normalized
paths, checks that the path is a file, then calls the existing version runner.
Failure for an implicit candidate produces a warning and continues. Failure for
an explicit `--codex-bin` remains fatal because the user selected it directly.

Candidate sources include `explicit`, `path`, `npm`, `home-local`,
`homebrew`, `desktop-install`, and `running-process`.

## Process adapters

Define a process record interface that is easy to mock in unit tests. The
production adapter uses:

- Windows: PowerShell/CIM JSON output for `Win32_Process`, decoded as UTF-8;
- POSIX: `ps -axo pid=,comm=,args=` as a read-only best-effort source.

Only records whose executable basename is a Codex executable and whose parsed
argument tokens contain `app-server` qualify. Process inspection catches all
OS/subprocess/JSON failures and returns an empty list plus a redacted warning.

Windows AppX installation discovery uses a read-only PowerShell query when
available. Registered application paths are candidates, but Store ACL failure
does not abort discovery.

## Home evidence

Return source together with the resolved path. Marker names:

```text
config.toml
state_5.sqlite
sessions
archived_sessions
```

Confidence is deterministic:

- `high`: explicit or environment home, or two or more markers;
- `medium`: one marker;
- `low`: default path with no markers.

Home evidence never follows the parent of a CLI or desktop executable.

## CLI behavior

The preflight prints three separate sections/lines for runnable CLI, desktop
backend, and Codex home. Setup records the detection result before token input.

When `Y` is selected, the CLI checks `desktop_process` before candidate TOML or
catalog construction is written. The transaction layer retains its SQLite
WAL/exclusive-lock guard as a second line of defense.

## Compatibility and rollback

No persisted format changes are introduced. If enhanced discovery fails, the
existing fallback model catalog and home defaults still work. Code changes are
limited to discovery, preflight/migration gates, tests, README, and the existing
Codex configurator contract.

## GitHub Releases bootstrap

`scripts/bootstrap.py` is standalone Python standard-library code and is
published as a release asset. It:

1. validates `OWNER/REPO` and the tag format;
2. calls the public GitHub Releases API for the selected tag/latest release;
3. locates exact bundle and `.sha256` asset names;
4. downloads to a temporary file with a size limit and verifies SHA-256;
5. validates ZIP paths (no traversal or symlink entries), extracts into a
   versioned cache, and checks required package markers;
6. runs the extracted package with `PYTHONPATH=<cache>/src`.

The bootstrap never receives the Xi-AI Key. All setup arguments after the
bootstrap options are forwarded to the local configurator. A release workflow
builds the ZIP and checksum and creates GitHub Release assets on `v*` tags.
