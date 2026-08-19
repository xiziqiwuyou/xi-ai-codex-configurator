# Repository impact research

## Endpoint surface

- `src/codex_configurator/endpoints.py` owns `ORIGIN`, `API_BASE`, `MODELS_URL`, and `RESPONSES_URL`.
- `remote_models.py` consumes `MODELS_URL`; `toml_merge.py` consumes `API_BASE`.
- Exact endpoint assertions exist in `tests/test_endpoints_discovery.py`, `tests/test_credentials_remote.py`, and `tests/test_catalog_toml.py`.
- Active user documentation references the old domain in `README.md`. Archived Trellis tasks are historical evidence and should not be rewritten.

## Download surface

- `scripts/bootstrap.py:_read_limited` reads responses in bounded 1 MiB chunks and already sees `Content-Length` but emits no progress.
- `_open_bytes` owns three-attempt retry behavior; `_download` and `install_release` own checksum/bundle download and cache installation.
- `tests/test_bootstrap.py:FakeResponse` supports sized reads and response headers, so known/unknown length and callback assertions can be added without network access.
- README one-line commands already use `curl`; adding `--progress-bar` covers the initial bootstrap/checksum downloads before Python starts.

## Session merge surface

- `cli.py:_setup` owns the `Y/N` boundary, desktop shutdown/rechecks, scan invocation, plan summary, and call into the transaction.
- `sessions.py:collect_rollout_changes` scans `sessions` and `archived_sessions`, parsing only the first JSONL record.
- `transaction.py:create_backup` copies config, catalog, rollout files, and a `VACUUM INTO` SQLite snapshot.
- `transaction.py:apply_setup` owns readiness, backup, config/catalog writes, rollout rewrites, SQLite update, and automatic restore.
- Existing tests in `test_cli.py` and `test_sessions_transaction.py` cover `N` byte preservation, process gates, retained WAL handling, failure injection, backup, and rollback. Progress assertions should extend these seams rather than replace transaction tests.

## Minimal design conclusion

Use optional progress callbacks at existing loop and phase boundaries. Add one package-local renderer for configuration/session work and a self-contained renderer in the standalone bootstrap. Preserve all current safety checks and transaction ordering.
