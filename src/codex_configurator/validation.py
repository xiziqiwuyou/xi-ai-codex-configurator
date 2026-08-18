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
        raise ConfigurationError(f"Codex TOML 无效：{exc}") from exc


def parse_catalog(data: bytes) -> dict[str, Any]:
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"模型目录 JSON 无效：{exc}") from exc
    return validate_catalog(document)


def validate_installed(codex_home: Path) -> dict[str, Any]:
    config_path = codex_home / "config.toml"
    catalog_path = codex_home / "xi-ai-model-catalog.json"
    if not config_path.is_file():
        raise ConfigurationError(f"缺少 Codex 配置文件：{config_path}")
    config = parse_toml(config_path.read_bytes())
    providers = config.get("model_providers")
    if not isinstance(providers, dict):
        raise ConfigurationError("Codex 配置中缺少模型供应商")
    provider = providers.get(PROVIDER_ID)
    if not isinstance(provider, dict):
        raise ConfigurationError("Codex 配置中缺少 xi_ai 供应商")
    if config.get("model_provider") != PROVIDER_ID:
        raise ConfigurationError("Codex 当前未使用 xi_ai 供应商")
    if provider.get("base_url") != API_BASE or provider.get("wire_api") != "responses":
        raise ConfigurationError("xi_ai 供应商未使用 Xi-AI Responses 接口")
    configured_catalog = config.get("model_catalog_json")
    selected_catalog = Path(configured_catalog).expanduser() if configured_catalog else catalog_path
    if not selected_catalog.is_absolute():
        selected_catalog = codex_home / selected_catalog
    if not selected_catalog.is_file():
        raise ConfigurationError(f"缺少模型目录：{selected_catalog}")
    catalog = parse_catalog(selected_catalog.read_bytes())
    model = config.get("model")
    if not isinstance(model, str) or model not in {
        item["slug"] for item in catalog["models"]
    }:
        raise ConfigurationError("已配置的 Codex 模型不在模型目录中")
    return {
        "model": model,
        "provider": config.get("model_provider"),
        "catalog": selected_catalog,
        "model_count": len(catalog["models"]),
    }
