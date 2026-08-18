from __future__ import annotations

import json
import re
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .endpoints import MODELS_URL
from .errors import RemoteModelError


MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


def fetch_remote_model_ids(
    token: str,
    *,
    url: str = MODELS_URL,
    opener: Callable = urlopen,
    timeout: float = 20,
) -> list[str]:
    request = Request(
        url,
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = response.read()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise RemoteModelError("Xi-AI 拒绝了该 API Key，请检查后重试") from exc
        raise RemoteModelError(f"请求 Xi-AI 模型列表失败，HTTP {exc.code}") from exc
    except (URLError, OSError) as exc:
        raise RemoteModelError("无法连接 Xi-AI 模型接口") from exc

    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteModelError("Xi-AI 返回的模型列表不是有效 JSON") from exc

    data = document.get("data") if isinstance(document, dict) else None
    if not isinstance(data, list):
        raise RemoteModelError("Xi-AI 模型响应中缺少 data 数组")

    model_ids: list[str] = []
    seen: set[str] = set()
    for item in data:
        model_id = item.get("id") if isinstance(item, dict) else None
        if isinstance(model_id, str) and model_id.strip():
            normalized = model_id.strip()
            if not MODEL_ID_RE.fullmatch(normalized):
                continue
            if normalized not in seen:
                seen.add(normalized)
                model_ids.append(normalized)
    if not model_ids:
        raise RemoteModelError("Xi-AI 未返回可选择的模型")
    return model_ids
