# Technical Design

## Root Cause

`ensure_sqlite_ready()` currently treats the presence of either SQLite sidecar as proof of an active database. This confuses persistent database state with process ownership. A retained WAL may contain committed pages that have not reached the main database, so deleting or ignoring it is unsafe.

## Safety Model

The fix uses two independent gates:

1. Process ownership gate: `setup + Y` performs fresh desktop discovery after the optional close and again immediately before applying the transaction.
2. SQLite gate: only after the process gate passes may SQLite recover/checkpoint a retained WAL, validate logical integrity, and acquire a write transaction.

Neither gate is sufficient alone. Process discovery prevents an idle Codex connection from racing after a SQLite check. SQLite locking and checkpoint results cover writers/read transactions and validate the retained WAL.

## API Changes

Extend the readiness and transaction APIs with an explicit, default-off capability:

```python
def ensure_sqlite_ready(path: Path, *, allow_wal_recovery: bool = False) -> None

def apply_setup(
    codex_home: Path,
    changes: SetupChanges,
    *,
    fail_at: str | None = None,
    allow_wal_recovery: bool = False,
) -> Path
```

The CLI passes `allow_wal_recovery=True` only for normal `setup + Y` after fresh process verification. Direct callers and restore retain the default conservative behavior.

## SQLite Readiness Flow

When `allow_wal_recovery=False`, preserve the existing sidecar rejection before opening the database.

When `allow_wal_recovery=True`:

1. Open the database through SQLite without deleting or moving sidecars.
2. Set a bounded `busy_timeout`.
3. If journal mode is WAL, run `PRAGMA wal_checkpoint(RESTART)` and reject a non-zero busy result. RESTART waits for readers using the WAL before resetting the next writer position.
4. Run `PRAGMA quick_check` and require the single result `ok`.
5. Run `BEGIN IMMEDIATE`, then roll back, to verify that a write transaction can be acquired.
6. Close the connection and proceed to the existing `VACUUM INTO` backup.

Checkpointing may change the physical placement of already committed pages but does not alter logical user data. It is performed by SQLite itself and preserves crash recovery semantics.

## CLI Flow

For normal `setup + Y`:

1. If the initial discovery found a desktop backend, run the exact-instance close flow.
2. Always run fresh desktop discovery, even when no backend was initially detected.
3. Abort on inspection warnings or any matching backend.
4. Inspect schema and collect rollout changes.
5. Print the plan.
6. Run fresh discovery again immediately before `apply_setup`.
7. Call `apply_setup(..., allow_wal_recovery=True)`.

Dry-run reports planned behavior but does not close, checkpoint, recover, back up, or mutate.

## Backup And Rollback

The existing ordering remains: readiness gate, complete file and SQLite backup, then config/catalog/rollout/SQLite mutation. `VACUUM INTO` reads the logical database including committed WAL content. Any later failure restores the consistent SQLite snapshot and original files.

## Compatibility

- No dependency is added; Python's `sqlite3` module exposes all required pragmas.
- Existing direct callers keep the conservative default.
- Endpoint, model, TOML and session metadata formats do not change.
- Release version becomes `0.3.1`; asset names remain unchanged.

## Risks And Mitigations

- A process can restart after the first check: perform a second check immediately before transaction application.
- A checkpoint can be busy due to an active reader: inspect the returned busy field and abort.
- WAL may contain the newest committed data: never delete or copy the main file without WAL; use SQLite checkpoint and `VACUUM INTO`.
- Current-session real close would terminate this task: verify close logic with injected processes and leave the final `Y` run to the user in system PowerShell.

## Rollback Point

Reverting the explicit `allow_wal_recovery` path restores `v0.3.0` behavior. Storage formats and existing backups remain compatible.
