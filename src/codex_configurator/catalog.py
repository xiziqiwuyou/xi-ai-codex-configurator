from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .discovery import run_codex_json
from .errors import CatalogError, DiscoveryError


def validate_catalog(document: object) -> dict[str, Any]:
    if not isinstance(document, dict) or not isinstance(document.get("models"), list):
        raise CatalogError("模型目录必须是包含 models 数组的对象")
    seen: set[str] = set()
    for item in document["models"]:
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
            raise CatalogError("模型目录中的每个模型都必须包含字符串 slug")
        slug = item["slug"]
        if not slug or slug in seen:
            raise CatalogError("模型目录包含空白或重复的 slug")
        seen.add(slug)
    if not seen:
        raise CatalogError("模型目录为空")
    return document


def load_bundled_catalog(
    executable: Path | None,
    *,
    fallback_path: Path,
) -> dict[str, Any]:
    if executable:
        try:
            document = json.loads(run_codex_json(executable, ["debug", "models", "--bundled"]))
            return validate_catalog(document)
        except (CatalogError, DiscoveryError, json.JSONDecodeError):
            pass
    try:
        document = json.loads(fallback_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError("内置 Codex 模型目录不可用") from exc
    return validate_catalog(document)


def _generic_remote_entry(model_id: str, template: dict[str, Any], priority: int) -> dict[str, Any]:
    entry = copy.deepcopy(template)
    entry["slug"] = model_id
    entry["display_name"] = model_id
    entry["description"] = "Xi-AI 远程模型；能力信息使用保守的 Codex 模板。"
    entry["priority"] = priority
    entry["visibility"] = "list"
    entry["supported_in_api"] = True
    entry["input_modalities"] = ["text"]
    entry["supports_image_detail_original"] = False
    entry["supports_search_tool"] = False
    entry["web_search_tool_type"] = "text"
    entry["use_responses_lite"] = False
    entry["context_window"] = min(int(entry.get("context_window", 128000)), 128000)
    entry["max_context_window"] = min(int(entry.get("max_context_window", 128000)), 128000)
    entry["effective_context_window_percent"] = 95
    entry["additional_speed_tiers"] = []
    entry["service_tiers"] = []
    entry["availability_nux"] = None
    entry["upgrade"] = None
    return entry


def merge_catalog(bundled: dict[str, Any], remote_ids: list[str]) -> dict[str, Any]:
    validate_catalog(bundled)
    entries = copy.deepcopy(bundled["models"])
    existing = {item["slug"] for item in entries}
    template = entries[0]
    next_priority = max(int(item.get("priority", 1)) for item in entries) + 1
    for model_id in remote_ids:
        if model_id not in existing:
            entries.append(_generic_remote_entry(model_id, template, next_priority))
            existing.add(model_id)
            next_priority += 1
    return validate_catalog({"models": entries})


def catalog_bytes(document: dict[str, Any]) -> bytes:
    validate_catalog(document)
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
