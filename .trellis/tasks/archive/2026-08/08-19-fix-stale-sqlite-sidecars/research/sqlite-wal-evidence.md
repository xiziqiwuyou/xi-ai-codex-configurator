# SQLite WAL Evidence

## Repository reproduction

An isolated valid WAL database was copied as a three-file set while its source connection was open. No process held the copied paths. `ensure_sqlite_ready()` rejected the copy solely because `-wal` / `-shm` existed, while the existing `VACUUM INTO` backup succeeded, returned `integrity_check=ok`, and preserved the committed row stored through WAL.

## Official SQLite documentation

- WAL overview: https://www.sqlite.org/wal.html
  - A retained WAL can remain after all connections close when shutdown is not clean or persistent WAL is enabled.
  - The WAL is part of persistent database state and must remain with the main database.
  - RESTART checkpoints wait for readers using the WAL before allowing the next writer to restart the log.
- WAL file format: https://www.sqlite.org/walformat.html
  - The main database, WAL and SHM describe active WAL state; SHM coordinates locks and can be reconstructed from WAL.
- Transaction behavior: https://www.sqlite.org/lang_transaction.html
  - `BEGIN EXCLUSIVE` and `BEGIN IMMEDIATE` are equivalent in WAL mode, so EXCLUSIVE alone is not a no-reader proof.
- Backup API: https://www.sqlite.org/backup.html
  - SQLite's online backup API and `VACUUM INTO` produce consistent database snapshots.
- VACUUM INTO: https://www.sqlite.org/lang_vacuum.html#vacuuminto
  - The source database remains logically unchanged and the destination contains the same logical content.

## Design consequence

Sidecar existence is state evidence, not ownership evidence. The authorized migration path must combine fresh Codex process discovery with SQLite-native checkpoint, integrity and transaction checks. Manual sidecar deletion is forbidden.
