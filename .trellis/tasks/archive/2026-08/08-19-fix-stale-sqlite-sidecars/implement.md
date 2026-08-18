# Implementation Plan

1. Add failing regression tests before product changes.
   - Build a valid WAL database, copy main/WAL/SHM while open, then release all target handles.
   - Assert the current readiness check rejects it even though `VACUUM INTO` succeeds and preserves WAL data.
   - Add active WAL reader/busy coverage.
   - Add CLI tests for unconditional pre-migration rediscovery and a second pre-apply respawn check.
2. Implement the explicit recovery capability.
   - Add `allow_wal_recovery=False` to readiness and transaction APIs.
   - Preserve default sidecar rejection.
   - In the authorized branch, run RESTART checkpoint, quick check and immediate write-lock probe with bounded timeout.
3. Strengthen the CLI gate.
   - Rediscover after `Y` regardless of initial desktop detection.
   - Rediscover immediately before `apply_setup`.
   - Pass recovery authorization only after both checks succeed.
4. Update version and contracts.
   - Bump package/runtime version and README examples to `0.3.1`.
   - Document retained WAL semantics, process gates, checkpoint behavior and error matrix.
5. Verify locally.
   - Targeted failing tests turn green.
   - `python -m unittest discover -s tests -v` with `PYTHONPATH=src`.
   - `python -m compileall -q src scripts tests`.
   - PowerShell parser and README one-line parser.
   - Git Bash `sh -n` for setup and README command.
   - `git diff --check`, release packaging and manifest/hash inspection.
6. Commit, archive and publish.
   - Keep product, task archive and journal commits separate.
   - Push `master`, create and push `v0.3.1`.
   - Wait for Release workflow, verify all five remote assets and run published bootstrap in default `--detect-only` mode.

## Rollback

No user data format changes are introduced. If verification fails, do not tag; revert only the recovery authorization and CLI gate changes, leaving `v0.3.0` as the latest release.
