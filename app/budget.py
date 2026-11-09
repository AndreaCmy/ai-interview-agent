"""LLM 调用预算护栏：统计每次调用，超过上限直接抛出 BudgetExceeded，绝不无限调用。

同时是唯一调用出入口，所以 trace 的 LLM 事件挂在这里记 —— 一处收口，
命令行、Web、评测三条路径都能自动留痕。
"""
from __future__ import annotations

import time
from typing import Any, List, Optional

from .trace import get_tracer


class BudgetExceeded(RuntimeError):
    pass


class BudgetedLLM:
    """包一层，给任意 LLM 加调用次数上限。"""

    def __init__(self, inner: Any, max_calls: int, label: str = "LLM"):
        self.inner = inner
        self.max_calls = max(0, int(max_calls))
        self.calls = 0
        self.label = label

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.calls)

    def chat(self, messages: List[dict], tools: Optional[List[dict]] = None) -> Any:
        if self.calls >= self.max_calls:
            raise BudgetExceeded(
                f"{self.label} 调用次数已达上限 {self.max_calls}，已停止后续调用。"
            )
        self.calls += 1
        print(f"[{self.label} 调用 {self.calls}/{self.max_calls}]")
        tracer = get_tracer()
        started = time.monotonic()
        try:
            resp = self.inner.chat(messages, tools=tools)
        except Exception as exc:
            tracer.llm_call(
                model=getattr(self.inner, "model", ""),
                temperature=getattr(self.inner, "temperature", None),
                messages=messages,
                tools=tools,
                latency_ms=int((time.monotonic() - started) * 1000),
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                call_no=self.calls,
            )
            raise
        content = ""
        try:
            content = resp.choices[0].message.content or ""
        except (AttributeError, IndexError):
            content = ""
        tracer.llm_call(
            model=getattr(self.inner, "model", ""),
            temperature=getattr(self.inner, "temperature", None),
            messages=messages,
            tools=tools,
            latency_ms=int((time.monotonic() - started) * 1000),
            ok=True,
            response=content,
            call_no=self.calls,
        )
        return resp
