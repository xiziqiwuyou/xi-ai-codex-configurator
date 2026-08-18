from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from .endpoints import API_BASE, PROVIDER_ID
from .errors import ConfigurationError


MANAGED_ROOT_KEYS = {
    "model",
    "model_provider",
    "preferred_auth_method",
    "forced_login_method",
    "model_catalog_json",
}
TABLE_RE = re.compile(r"^\s*\[([^\]]+)\]")
ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _is_provider_header(line: str) -> bool:
    match = TABLE_RE.match(line)
    if not match:
        return False
    name = match.group(1).strip().replace('"', "").replace("'", "")
    provider = f"model_providers.{PROVIDER_ID}"
    return name == provider or name.startswith(f"{provider}.")


def merge_config(
    existing: str,
    *,
    model: str,
    catalog_path: Path,
    token: str,
) -> str:
    if "\x00" in existing:
        raise ConfigurationError("Codex 配置中包含 NUL 字节")
    source = existing.lstrip("\ufeff")
    lines = source.splitlines(keepends=True)
    first_table = next(
        (index for index, line in enumerate(lines) if TABLE_RE.match(line)), len(lines)
    )

    root = []
    for line in lines[:first_table]:
        assignment = ASSIGNMENT_RE.match(line)
        if assignment and assignment.group(1) in MANAGED_ROOT_KEYS:
            continue
        root.append(line)

    remainder: list[str] = []
    index = first_table
    while index < len(lines):
        if _is_provider_header(lines[index]):
            index += 1
            while index < len(lines) and not TABLE_RE.match(lines[index]):
                index += 1
            continue
        remainder.append(lines[index])
        index += 1

    managed_root = [
        f'model = {_toml_string(model)}\n',
        'model_provider = "xi_ai"\n',
        'preferred_auth_method = "apikey"\n',
        'forced_login_method = "api"\n',
        f'model_catalog_json = {_toml_string(catalog_path.as_posix())}\n',
    ]
    provider = [
        f'[model_providers.{PROVIDER_ID}]\n',
        'name = "Xi-AI"\n',
        f'base_url = {_toml_string(API_BASE)}\n',
        'wire_api = "responses"\n',
        f'experimental_bearer_token = {_toml_string(token)}\n',
    ]

    content = "".join(root).rstrip("\r\n")
    content = f"{content}\n" if content else ""
    content += "".join(managed_root)
    if remainder:
        content += "\n" + "".join(remainder).lstrip("\r\n")
    content = content.rstrip("\r\n") + "\n\n" + "".join(provider)
    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"生成的 Codex TOML 无效：{exc}") from exc
    return content
