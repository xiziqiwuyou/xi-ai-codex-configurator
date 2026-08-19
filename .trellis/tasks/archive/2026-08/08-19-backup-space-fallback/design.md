# Technical design: compact backups and alternate volumes

## Boundaries

The change is limited to the transaction/backup layer, session rollout
metadata, CLI argument and prompt handling, documentation, and tests. The
Codex configuration schema and Xi-AI API flow remain unchanged.

## Backup format

New backups use manifest `version: 2` and retain the existing top-level
provider, CODEX_HOME, file list, SQLite entry, and migration flag. Each file
entry has one of these forms:

```json
{"path":"config.toml","existed":true,"kind":"full",
 "backup":"files/config.toml","sha256":"...","mtime_ns":123}
```

```json
{"path":"sessions/2026/08/19/a.jsonl","existed":true,
 "kind":"rollout_first_line","backup":"rollouts/000001.patch",
 "sha256":"<original first-line sha256>","size":123,"mtime_ns":123}
```

Absent files keep `existed: false`. Legacy manifests without `kind` or with
`version: 1` continue to mean full-file entries. A compact patch file contains
the exact original first line as bytes; its own SHA-256 and byte length are
recorded in the manifest. The patch filename is generated from an ordinal,
never from a user/session path.

## Restore and rollback

`restore_backup` validates all manifest entries and patch hashes before
writing. For a compact rollout entry it atomically rewrites the current file:
read and discard its current first line, write the stored original first line,
then stream the untouched remainder. The recorded mtime is restored. This
keeps message/event content untouched and supports both automatic rollback and
manual restore. Full legacy entries use the existing byte-for-byte path.

## Backup destination and space preflight

`SetupChanges` remains the mutation description. `create_backup` and
`apply_setup` gain an optional `backup_root`. If absent, the default is
`codex_home / "backup-xi-ai"`; if present, it is resolved and created under
the caller's chosen volume. A custom root must not be inside CODEX_HOME (the
default root is the only in-home exception).

Before creating a timestamp directory, calculate:

- exact sizes of full config/catalog files that exist;
- exact original first-line sizes for compact rollout patches;
- current SQLite size when a session migration is requested;
- manifest/patch overhead and a safety margin;
- the largest source file needed for an atomic rewrite.

Use `shutil.disk_usage` on the nearest existing destination parent. The check
is repeated immediately before copying/SQLite snapshot creation. A
`TransactionError` reports required and available bytes. The check is a
precondition; backup errors still clean their partial directory.

The CLI accepts `--backup-root`. Without it, if the default check fails, it
prints candidate mounted volumes with enough space and asks once for a custom
path. A blank answer aborts. Dry-run only reports the estimate and does not
prompt or create directories. The transaction layer repeats the check so
library callers cannot bypass it.

## Latest-backup lookup

`latest_backup(codex_home, backup_root=None)` uses the default root or the
explicit root. `restore --backup-root PATH` selects the latest backup there;
`restore --backup PATH` remains an exact-path override.

## Compatibility and rollback

No old backup is migrated or deleted. Manifest loading accepts v1 and v2,
validates the matching CODEX_HOME, safe relative source paths, hashes, and
unique targets. External backup roots are allowed only when explicitly
selected by the caller; target paths still cannot escape CODEX_HOME or point
into its backup directory.

## Failure behavior

Space, permissions, malformed patches, or integrity failures occur before
business-data writes. Any later mutation failure invokes the same restore
routine and reports a secondary restore error if recovery itself fails.
