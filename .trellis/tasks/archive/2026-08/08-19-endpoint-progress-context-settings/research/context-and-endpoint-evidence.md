# Codex context and Xi-AI endpoint evidence

## Local Codex evidence

Environment checked on 2026-08-19:

- `codex-cli 0.144.1`
- Generated app-server JSON schema defines both `model_context_window` and `model_auto_compact_token_limit` as nullable integer values with `int64` format.
- `codex debug models -c model_context_window=500000 -c model_auto_compact_token_limit=450000` succeeds.
- Supplying a string to either field fails with `invalid type ... expected i64`, confirming strict integer parsing.
- The bundled model catalog contains `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`. In this installed build, each bundled entry reports a 372000 context window, so 500K and 1M are explicit overrides rather than defaults.
- `codex app-server --strict-config` rejects the legacy top-level `preferred_auth_method`; removing it while retaining `forced_login_method`, the provider table, and integer context keys avoids the unknown-field error.

The generated schema was inspected from:

```text
%TEMP%/codex-schema-inspect-20260819/
```

## Official documentation limitation

The OpenAI Codex manual helper, OpenAI Developer Docs MCP, and direct `developers.openai.com` config-reference requests all returned HTTP 403 in this environment. Therefore:

- The field validity and type are grounded in the locally installed official Codex binary/schema.
- The 500K recommendation, 1M example, 272K billing threshold, and price multipliers are treated as user-supplied/service-policy guidance, not as independently verified OpenAI documentation.
- User-facing text must say that billing behavior is subject to the provider's current rules.

## Xi-AI endpoint check

Unauthenticated connectivity checks on 2026-08-19 reached the service and returned authentication failures:

```text
GET  https://api.xi-ai.net/v1/models    -> HTTP 401
POST https://api.xi-ai.net/v1/responses -> HTTP 401
```

This verifies DNS, TLS, route existence, and authentication enforcement without transmitting a real API Key. It does not validate an authenticated model request.
