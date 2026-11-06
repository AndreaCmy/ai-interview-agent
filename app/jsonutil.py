"""从 LLM 文本里稳妥地抽 JSON（兼容 ```json 围栏、前后有解释文字的情况）。"""
from __future__ import annotations

import json
import re
from typing import Any


def extract_json(text: str) -> Any:
    if not text or not text.strip():
        raise ValueError("empty LLM output")

    s = text.strip()

    # 1) 先剥 ```json ... ``` 围栏
    m = re.search(r"```[a-zA-Z]*\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()

    # 2) 直接尝试
    try:
        return json.loads(s)
    except Exception:
        pass

    # 3) 兜底：从第一个 { / [ 截到最后一个 } / ]
    starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if not starts:
        raise ValueError(f"no JSON found in: {text[:120]}")
    start = min(starts)
    end = max(s.rfind("}"), s.rfind("]"))
    if end <= start:
        raise ValueError(f"unbalanced JSON in: {text[:120]}")
    return json.loads(s[start:end + 1])
