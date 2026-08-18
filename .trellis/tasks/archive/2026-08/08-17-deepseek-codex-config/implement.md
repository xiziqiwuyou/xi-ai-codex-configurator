# Implementation plan

## Ordered work

1. Create the Python package, module entry point, exit-code contract, and
   PowerShell/POSIX launchers.
2. Implement Codex executable/config-home discovery and version/schema guards.
3. Implement fixed Xi-AI endpoint construction and the Enter + one-time hidden
   token flow.
4. Implement authenticated `/v1/models` retrieval, response validation,
   deduplication, and terminal model selection.
5. Implement bundled catalog extraction with `codex debug models --bundled`,
   the fallback snapshot, conservative remote entries, and final catalog
   validation.
6. Implement the managed TOML merge for `xi_ai`, selected model, authentication
   mode, and merged catalog path.
7. Implement backup manifests, hashing, temporary files, atomic replacement,
   failpoint injection, and restore for config/catalog changes.
8. Implement the approved `Y/N` branch:
   rollout scan/update, SQLite schema checks, consistent backup, visibility
   metadata transaction, timestamp preservation, rollback, and restart notice.
9. Add README examples and a security section distinguishing local visibility
   migration from remote conversation upload.
10. Run full tests, a temporary-CODEX_HOME end-to-end setup, strict config
   validation, and Trellis quality/spec-update steps.

## Test matrix

- Windows, POSIX, `CODEX_HOME`, npm install, desktop-only fallback, and missing
  installation discovery.
- Endpoint origin normalization and exact `/v1/models`/`/v1/responses` paths.
- Enter gate, exactly one hidden prompt, empty token, 401, malformed JSON, and
  network failure without secret leakage.
- Bundled catalog retention, remote append, collisions, duplicate remote ids,
  invalid generic metadata, and selected-model persistence.
- Existing TOML with comments, profiles, MCP, hooks, other providers, an
  existing Xi-AI table, malformed TOML, and UTF-8/BOM handling.
- `N` causes zero session writes.
- `Y` handles legacy/current rollout metadata, active/archived sessions,
  supported/unsupported SQLite schemas, locked databases, WAL mode, timestamps,
  visibility flags, and full rollback at each injected failure point.
- Restore rejects incomplete, outside-root, hash-mismatched, and secret-bearing
  manifests.

## Validation commands

```powershell
python -m unittest discover -s tests -v
python -m compileall src tests
$env:PYTHONPATH = "src"
python -m codex_configurator --help
python -m codex_configurator validate --codex-home work/test-codex-home
codex --strict-config --version
git diff --check
```

POSIX launcher validation:

```sh
sh -n scripts/setup.sh
PYTHONPATH=src python3 -m codex_configurator --help
```

## Safety gates

- Never test writes against the real user `.codex` directory.
- Never print or fixture a real token.
- Do not implement remote history upload without a separate approved contract.
- The meaning of `Y` is fixed for this task: perform only the reversible local
  provider-visibility migration; never upload or replay historical content.
