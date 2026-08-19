# Safe backups when the Codex home volume is low on space

## Goal

Allow setup and local conversation migration to proceed safely when the Codex
home volume (usually C:) cannot hold a full second copy of all rollout
history. The tool must preserve the transaction guarantee: no business files
are changed unless a usable backup has been created, and every failed
mutation can be rolled back.

## Confirmed background

- The current backup implementation copies every JSONL rollout file in full
  into `<CODEX_HOME>/backup-xi-ai/<timestamp>/`.
- A real Codex home can contain many gigabytes of historical sessions while
  migration changes only the first `session_meta` line of each selected file.
- The SQLite database still requires a complete `VACUUM INTO` snapshot because
  provider visibility changes are made in the database.
- Existing full backups already created by earlier versions must remain
  restorable.

## Requirements

1. Replace full rollout-file copies in new backups with a compact, versioned
   first-line patch containing the original first line, file metadata, and
   integrity information. Config, catalog, manifest, and SQLite snapshots
   remain complete backups.
2. Restore and automatic rollback must understand both legacy full-file
   backups and the new compact format. Restoring a compact entry must preserve
   all untouched session content byte-for-byte.
3. Add an optional `--backup-root PATH` to setup and restore. The default
   remains `<CODEX_HOME>/backup-xi-ai`; an external path may be on another
   mounted drive.
4. Before mutation, estimate backup space and check the destination volume.
   Include a safety margin and the temporary space needed for SQLite and
   atomic rollout rewrites. Report required and available space without
   revealing tokens or conversation contents.
5. If the default destination is insufficient and no explicit path was given,
   show available alternate volumes/directories and ask for a backup path.
   Empty input cancels setup. A dry run reports the condition but never writes
   or creates a backup.
6. If an explicit backup path is insufficient or inaccessible, fail before any
   target configuration/session/database write. Never silently skip the
   backup.
7. Keep restore path validation, manifest integrity checks, and rollback
   cleanup. Existing `restore --backup PATH` remains supported.
8. Document the compact format, low-space behavior, alternate-drive usage,
   and recovery commands in the backend spec and README.

## Acceptance criteria

- A migration with thousands of rollout files creates a backup whose rollout
  portion scales with changed first-line sizes rather than full history size.
- A forced low-space check aborts before `config.toml`, catalog, rollout, or
  SQLite writes and gives a Chinese actionable error.
- Supplying `--backup-root` on another volume creates the backup there and
  `restore --backup-root` can find and restore the latest backup.
- Automatic rollback after injected config/catalog/rollout/SQLite failures
  restores exact original bytes for both compact and legacy backup entries.
- Compact restore changes only the original first line and preserves all later
  session bytes and timestamps.
- Invalid/traversal backup paths, corrupted patch files, duplicate targets,
  and insufficient space are rejected before business-data mutation.
- Existing tests remain green and new tests cover estimation, volume fallback,
  compact backup/restore, legacy compatibility, and CLI prompting.

## Out of scope

- Deleting or compressing existing user backups automatically.
- Uploading backups or conversation data to Xi-AI, FTP, or any remote service.
- Changing the session migration fields or Codex provider semantics.

## Open questions

None. The approved design uses compact rollout patches by default and an
explicit alternate backup root when the default volume is insufficient.
