# Implementation plan

## Ordered work

1. Add source-aware Codex-home resolution and marker/confidence calculation.
2. Add candidate records and expand package-manager/desktop install paths for
   Windows, macOS, and Linux without changing explicit override semantics.
3. Add mockable process records and best-effort Windows/POSIX process adapters.
4. Classify only `codex ... app-server` as the desktop backend and retain
   inaccessible paths as non-runnable desktop evidence.
5. Refactor discovery to validate implicit candidates in priority order,
   continue after inaccessible/broken candidates, and collect safe warnings.
6. Expand `DiscoveryResult` with defaults to preserve existing callers/tests.
7. Update preflight output, add the no-prompt/no-network `--detect-only` real
   machine test mode, and reject `Y` before target writes when the desktop
   backend is active.
8. Update README and the backend executable contract.
9. Add unit/regression tests for candidate priority, process filtering,
   inaccessible Store paths, home confidence, preflight, and migration gating.
10. Add the standalone GitHub Releases bootstrap, release packager, checksum
    verification, ZIP safety checks, cache extraction, and release workflow.
11. Add bootstrap tests using mocked GitHub release assets and verify
    `--detect-only` forwarding without a Key or target writes.
12. Run the full Trellis quality/spec/commit/finish sequence.

## Validation commands

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m compileall src tests
python -m codex_configurator --help
git diff --check
```

```sh
sh -n scripts/setup.sh
PYTHONPATH=src python3 -m codex_configurator --help
```

## Safety and rollback points

- Never inspect or mutate real user state in tests.
- Never terminate a process.
- Keep process enumeration best-effort and non-fatal.
- Do not derive `CODEX_HOME` from executable parents.
- Preserve the existing strict session and transaction tests.
- If refactoring discovery breaks explicit overrides, revert the candidate
  pipeline before changing CLI behavior.
