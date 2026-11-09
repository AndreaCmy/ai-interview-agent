"""LLM 客户端：真实（DeepSeek / OpenAI 兼容）与桩（无 key 的确定性假模型）。

对照仓库步骤 1 的 `anthropic.Anthropic()`，这里换成 OpenAI 兼容的 client，
工具 schema 也跟着从 anthropic 的 input_schema 换成 OpenAI 的 function 格式。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional

from .config import get_settings

try:  # 真实调用才需要 openai，stub 模式可完全不装
    from openai import OpenAI  # type: ignore
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore


class RealLLM:
    """真实 LLM：OpenAI 兼容接口（DeepSeek）。"""

    def __init__(self) -> None:
        s = get_settings()
        if OpenAI is None:
            raise RuntimeError("openai 未安装：pip install -r requirements.txt")
        if not s["api_key"]:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY：请复制 .env.example 为 .env 并填入")
        self.client = OpenAI(api_key=s["api_key"], base_url=s["base_url"])
        self.model = s["model"]
        self.temperature = s.get("temperature") or None

    def chat(self, messages: List[dict], tools: Optional[List[dict]] = None) -> Any:
        kwargs: dict = {"model": self.model, "messages": messages}
        if self.temperature is not None:
            kwargs["temperature"] = float(self.temperature)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return self.client.chat.completions.create(**kwargs)


class StubLLM:
    """无需 API Key 的确定性假模型：第一次先触发一次工具调用，拿到工具结果后给最终回答。

    用于在没有 key 的情况下验证「循环 + 工具调度」是否跑通（对应仓库步骤 5 的 stub LLM 思路）。
    """

    model = "stub"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: List[dict], tools: Optional[List[dict]] = None) -> Any:
        self.calls += 1
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        if tools and not has_tool_result:
            name = tools[0]["function"]["name"]
            arg_obj = {p: "stub" for p in tools[0]["function"]["parameters"].get("properties", {})}
            import json as _json

            tc = SimpleNamespace(
                id="call_stub_1",
                type="function",
                function=SimpleNamespace(name=name, arguments=_json.dumps(arg_obj, ensure_ascii=False)),
            )
            msg = SimpleNamespace(content="（stub）先取一道题。", tool_calls=[tc])
        else:
            msg = SimpleNamespace(
                content="（stub）这是模拟面试官的追问：请结合一个具体项目讲讲你的做法。",
                tool_calls=[],
            )
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
