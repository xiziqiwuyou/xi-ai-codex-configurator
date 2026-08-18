# Endpoint, session, and model evidence

## Xi-AI endpoint probes

Read-only/unauthenticated probes on 2026-08-17 established route existence:

- `GET https://api.xi-ai.cn/v1/models` returned `401` JSON.
- `POST https://api.xi-ai.cn/v1/responses` with `{}` returned `401` JSON.
- `POST https://api.xi-ai.cn/v1/chat/completions` with `{}` returned `401` JSON.
- `GET https://api.xi-ai.cn/responses` returned the website HTML, so the Codex
  provider must include `/v1` in `base_url`.

No token was sent and no inference request was made.

## Local Codex evidence

- Installed CLI: `codex-cli 0.144.1`.
- `codex debug models --bundled` is available and returned eight bundled models.
- `codex debug models` returned ten models, exactly matching the active custom
  catalog, which demonstrates that a generated custom catalog must include the
  bundled entries if they should remain selectable.
- `codex resume --all` and `codex fork --all` are available.
- `state_5.sqlite` has a `threads` table with `model_provider`, `cwd`, `title`,
  `rollout_path`, visibility, source, and model metadata.
- The local database contained provider-specific thread groups, confirming that
  provider metadata affects session visibility.

## Existing implementation evidence

The local reference repository
`C:/Users/56252/Documents/Codex/2026-06-01/codex-ip-43-160-193-224/cockpit-tools-src`
contains a tested session visibility repair implementation in
`src-tauri/src/modules/codex_session_visibility.rs`.

Its behavior:

- backs up all affected rollout files;
- creates a consistent SQLite backup with `VACUUM INTO`;
- updates rollout `session_meta.payload.model_provider`;
- updates `threads.model_provider` and required visibility fields;
- preserves/restores rollout timestamps;
- records the target provider in a manifest;
- provides rollback tests.

This supports a reversible local visibility migration. It does not provide an
API for uploading local histories to a remote Responses service.

## Documentation availability note

The OpenAI Codex manual helper and official documentation MCP both returned HTTP
403 in this environment. Current behavior was therefore verified against the
installed Codex CLI and local Codex state/schema. The implementation must keep
version checks and fail closed when an unsupported schema is detected.
