# Implementation Plan

1. Lock current behavior and new safety boundaries in tests.
   - Add parent/root process fixtures for Windows and POSIX.
   - Replace the current active-desktop rejection test with close-success, close-failure and respawn cases.
   - Add `N`, `--dry-run`, `--detect-only` and self-descendant no-close assertions.
2. Extend discovery metadata.
   - Capture parent PIDs.
   - Derive the exact desktop root inside the same install root.
   - Preserve executable discovery and `CODEX_HOME` independence.
3. Implement the desktop control module.
   - Revalidate process identity before signaling.
   - Request graceful close and wait 15 seconds.
   - Revalidate again, then force the exact target after timeout and wait for exit.
   - Return structured close status for Chinese CLI output.
4. Integrate setup flow.
   - Trigger only after `Y`.
   - Keep detect-only/dry-run/N side-effect free.
   - Rediscover after close and abort on persistence/respawn.
   - Preserve the pre-write validation and transaction boundary.
5. Add verified one-line usage.
   - Add copy-ready PowerShell and POSIX commands with fixed repository, explicit `latest`, retries and SHA-256 validation.
   - Keep the existing bootstrap contract; do not add remote pipe-to-shell execution.
   - Test command construction and checksum-failure behavior where practical.
6. Update README and `.trellis/spec/backend/codex-configurator.md` with the new lifecycle, self-descendant restriction, force fallback, one-line entry and error matrix.
7. Verify.
   - `python -m unittest discover -s tests -v` with `PYTHONPATH=src`.
   - `python -m compileall -q src scripts tests`.
   - PowerShell parser check for `scripts/setup.ps1`.
   - `sh -n scripts/setup.sh` when Git Bash is available.
   - Real-machine `--detect-only` only; do not terminate the currently running Codex development session.

## Rollback Point

All process-control behavior is isolated before transaction creation. Reverting the desktop-control integration restores the current safe abort-on-active-desktop behavior without changing storage formats or backups.
