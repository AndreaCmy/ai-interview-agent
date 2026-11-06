"""LLM 调用预算护栏：统计每次调用，超过上限直接抛出 BudgetExceeded，绝不无限调用。"""
from __future__ import annotations

from typing import Any, List, Optional


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
        return self.inner.chat(messages, tools=tools)
