"""最小 ReAct 循环（手写核心）。

对照仓库步骤 1：while 循环 + stop 条件退出 + 工具分发。
这里用 OpenAI 兼容格式：assistant 的 tool_calls → 逐个执行 → 以 role=tool 回喂。
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from .trace import get_tracer


def run_agent(
    llm: Any,
    system: str,
    user_msg: str,
    tool_schemas: List[dict],
    tool_impls: Dict[str, Callable],
    max_iter: int = 8,
    verbose: bool = True,
) -> dict:
    messages: List[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]

    for it in range(1, max_iter + 1):
        resp = llm.chat(messages, tools=tool_schemas)
        msg = resp.choices[0].message
        content = getattr(msg, "content", None)
        tool_calls = getattr(msg, "tool_calls", None)

        # 1) 追加 assistant 消息（含 tool_calls）
        asst: dict = {"role": "assistant", "content": content or ""}
        if tool_calls:
            asst["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ]
        messages.append(asst)

        if verbose:
            snippet = (content or "").strip().replace("\n", " ")[:100]
            print(f"[iter {it}] thought: {snippet or '(tool call)'}")

        # 2) 没有工具调用 → 结束
        if not tool_calls:
            return {"iterations": it, "final": content, "messages": messages}

        # 3) 逐个执行工具，结果以 role=tool 回喂
        for tc in tool_calls:
            name = tc.function.name
            fn = tool_impls.get(name)
            started = time.monotonic()
            ok = True
            err = ""
            try:
                args = json.loads(tc.function.arguments or "{}")
                out = fn(**args) if fn else f"unknown tool: {name}"
                ok = fn is not None
            except Exception as e:  # is_error 回喂（对照仓库步骤 2）
                ok = False
                err = f"{type(e).__name__}: {e}"
                out = f"error: {e}"
            get_tracer().tool_call(
                name=name,
                args=tc.function.arguments,
                latency_ms=int((time.monotonic() - started) * 1000),
                ok=ok,
                result=out,
                error=err,
            )
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(out)})
            if verbose:
                print(f"    -> tool {name}({tc.function.arguments}) => {str(out)[:100]}")

    return {"iterations": max_iter, "final": None, "messages": messages}
