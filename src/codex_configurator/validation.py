from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from .catalog import validate_catalog
from .endpoints import API_BASE, PROVIDER_ID
from .errors import ConfigurationError


def parse_toml(data: bytes) -> dict[str, Any]:
    try:
        return tomllib.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"Codex TOML is invalid: {exc}") from exc


def parse_catalog(data: bytes) -> dict[str, Any]:
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Model catalog JSON is invalid: {exc}") from exc
    return validate_catalog(document)


def validate_installed(codex_home: Path) -> dict[str, Any]:
    config_path = codex_home / "config.toml"
    catalog_path = codex_home / "xi-ai-model-catalog.json"
    if not config_path.is_file():
        raise ConfigurationError(f"Missing Codex config: {config_path}")
    config = parse_toml(config_path.read_bytes())
    providers = config.get("model_providers")
    if not isinstance(providers, dict):
        raise ConfigurationError("Codex config does not contain model providers")
    provider = providers.get(PROVIDER_ID)
    if not isinstance(provider, dict):
        raise ConfigurationError("Codex config does not contain the xi_ai provider")
    if config.get("model_provider") != PROVIDER_ID:
        raise ConfigurationError("Codex is not currently using the xi_ai provider")
    if provider.get("base_url") != API_BASE or provider.get("wire_api") != "responses":
        raise ConfigurationError("xi_ai provider does not use the Xi-AI Responses endpoint")
    configured_catalog = config.get("model_catalog_json")
    selected_catalog = Path(configured_catalog).expanduser() if configured_catalog else catalog_path
    if not selected_catalog.is_absolute():
        selected_catalog = codex_home / selected_catalog
    if not selected_catalog.is_file():
        raise ConfigurationError(f"Missing model catalog: {selected_catalog}")
    catalog = parse_catalog(selected_catalog.read_bytes())
    model = config.get("model")
    if not isinstance(model, str) or model not in {
        item["slug"] for item in catalog["models"]
    }:
        raise ConfigurationError("Configured Codex model is not present in the model catalog")
    return {
        "model": model,
        "provider": config.get("model_provider"),
        "catalog": selected_catalog,
        "model_count": len(catalog["models"]),
    }
