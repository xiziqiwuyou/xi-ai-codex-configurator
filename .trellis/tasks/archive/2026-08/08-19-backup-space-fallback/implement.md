# Implementation plan

1. Extend `RolloutChange` with the original first line and add a safe atomic
   first-line restore helper.
2. Refactor transaction backup creation to emit v2 compact rollout entries,
   preserve full config/catalog/SQLite snapshots, accept `backup_root`, and
   validate destination space before and during backup.
3. Make manifest loading/restoration compatible with v1 full entries and v2
   compact entries, including patch integrity and external-root handling.
4. Add CLI `--backup-root`, low-space candidate discovery, user prompt,
   dry-run reporting, and backup-root-aware latest restore lookup.
5. Add regression tests for compact size/bytes, restore and rollback, low-space
   rejection/fallback, custom roots, legacy backups, and CLI behavior.
6. Update README and `.trellis/spec/backend/codex-configurator.md` with the
   new contracts and commands.
7. Run targeted tests, full unittest discovery, compileall, syntax checks, and
   `git diff --check`.

## Risk points

- A compact patch must never be applied if its target is missing or its
  original first-line hash does not match the manifest.
- Restore must validate every source before changing any target.
- The space check must not print full conversation paths or secrets.
- Existing v1 backups must remain usable.

## Validation commands

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m compileall src scripts tests
git diff --check
```
