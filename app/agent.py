"""最小 ReAct 循环（手写核心）。

对照仓库步骤 1：while 循环 + stop 条件退出 + 工具分发。
这里用 OpenAI 兼容格式：assistant 的 tool_calls → 逐个执行 → 以 role=tool 回喂。
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from .tools import validate_args
from .trace import get_tracer


def run_agent(
    llm: Any,
    system: str,
    user_msg: str,
    tool_schemas: List[dict],
    tool_impls: Dict[str, Callable],
    max_iter: int = 8,
    verbose: bool = True,
    max_repeats: int = 2,
) -> dict:
    """循环的返回值固定带 stop_reason，上层不用再猜「为什么停了」。

    stop_reason 取值：
      answered            模型给出最终回答（没有 tool_calls）
      max_iter            迭代上限用尽，final=None
      repeated_tool_call  同一工具同一参数连续重复调用，判定模型卡死
    预算异常（BudgetExceeded）不在这里吞掉，直接抛给调用方处理。
    """
    messages: List[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]

    seen: Dict[tuple, int] = {}      # 工具调用签名 → 出现次数，用于识别「原地打转」
    repeats = 0

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
            return {"iterations": it, "final": content, "messages": messages, "stop_reason": "answered"}

        # 3) 逐个执行工具，结果以 role=tool 回喂
        for tc in tool_calls:
            name = tc.function.name
            fn = tool_impls.get(name)
            started = time.monotonic()
            ok = True
            err = ""

            # 同一工具 + 同一参数重复：不执行第二次，塞一条提示让模型换路或收尾。
            # 只加 max_iter 治不了原地打转——模型会用同样的调用把 8 轮烧完。
            sig = (name, tc.function.arguments or "{}")
            seen[sig] = seen.get(sig, 0) + 1
            if seen[sig] > 1:
                repeats += 1
                msg = "重复调用：该工具与相同参数已经执行过，请换参数、换工具，或直接给出最终回答。"
                out = f"error: {msg}"
                ok = False
                err = "repeated_tool_call"
                get_tracer().tool_call(
                    name=name, args=tc.function.arguments, latency_ms=0, ok=False, result=out, error=err
                )
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})
                if verbose:
                    print(f"    -> tool {name} 重复调用，已拒绝执行 ({repeats}/{max_repeats})")
                if repeats >= max_repeats:
                    return {
                        "iterations": it,
                        "final": content,
                        "messages": messages,
                        "stop_reason": "repeated_tool_call",
                    }
                continue

            try:
                # 执行前校验：类型/枚举/必填由 schema 推导出的 pydantic 模型把关，
                # 失败会转成 `error: ...` 回喂给模型，走和工具抛异常同一条路径
                args = validate_args(tool_schemas, name, tc.function.arguments or "{}")
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

    return {"iterations": max_iter, "final": None, "messages": messages, "stop_reason": "max_iter"}
