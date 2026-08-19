from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .endpoints import API_BASE, PROVIDER_ID
from .errors import ConfigurationError


MANAGED_ROOT_KEYS = {
    "model",
    "model_provider",
    "preferred_auth_method",
    "forced_login_method",
    "model_catalog_json",
}
CONTEXT_ROOT_KEYS = {
    "model_context_window",
    "model_auto_compact_token_limit",
}
TABLE_RE = re.compile(r"^\s*\[([^\]]+)\]")
ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")


@dataclass(frozen=True)
class ContextConfig:
    mode: Literal["preserve", "set", "clear"] = "preserve"
    model_context_window: int | None = None
    model_auto_compact_token_limit: int | None = None

    def __post_init__(self) -> None:
        values = (self.model_context_window, self.model_auto_compact_token_limit)
        if self.mode == "set":
            if not all(type(value) is int and value > 0 for value in values):
                raise ValueError("set 模式需要两个正整数上下文值")
            assert self.model_context_window is not None
            assert self.model_auto_compact_token_limit is not None
            if self.model_auto_compact_token_limit >= self.model_context_window:
                raise ValueError("自动压缩阈值必须小于上下文窗口")
        elif self.mode in {"preserve", "clear"}:
            if any(value is not None for value in values):
                raise ValueError(f"{self.mode} 模式不能包含上下文值")
        else:
            raise ValueError(f"不支持的上下文配置模式：{self.mode}")


PRESERVE_CONTEXT = ContextConfig()
CLEAR_CONTEXT = ContextConfig(mode="clear")
CONTEXT_500K = ContextConfig(
    mode="set",
    model_context_window=500_000,
    model_auto_compact_token_limit=450_000,
)
CONTEXT_1M = ContextConfig(
    mode="set",
    model_context_window=1_000_000,
    model_auto_compact_token_limit=900_000,
)


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
    context: ContextConfig = PRESERVE_CONTEXT,
) -> str:
    if "\x00" in existing:
        raise ConfigurationError("Codex 配置中包含 NUL 字节")
    source = existing.lstrip("\ufeff")
    lines = source.splitlines(keepends=True)
    first_table = next(
        (index for index, line in enumerate(lines) if TABLE_RE.match(line)), len(lines)
    )

    managed_root_keys = set(MANAGED_ROOT_KEYS)
    if context.mode != "preserve":
        managed_root_keys.update(CONTEXT_ROOT_KEYS)

    root = []
    for line in lines[:first_table]:
        assignment = ASSIGNMENT_RE.match(line)
        if assignment and assignment.group(1) in managed_root_keys:
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
        'forced_login_method = "api"\n',
        f'model_catalog_json = {_toml_string(catalog_path.as_posix())}\n',
    ]
    if context.mode == "set":
        managed_root.extend(
            [
                f"model_context_window = {context.model_context_window}\n",
                "model_auto_compact_token_limit = "
                f"{context.model_auto_compact_token_limit}\n",
            ]
        )
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
