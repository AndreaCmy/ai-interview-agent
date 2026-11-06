"""工具注册表 + schema 生成。

对照仓库步骤 1 的 `@tool` 装饰器 + `inspect.signature` 生成 schema，
这里改成生成 OpenAI 兼容的 `{"type":"function","function":{...}}` 格式。
"""
from __future__ import annotations

import inspect
from typing import Callable, Dict, List

TOOLS: Dict[str, Callable] = {}

# 极简题库（P0 先用内嵌题；后续接 interview-qa-standard.md 做检索）
_SEED = [
    "请做一个 60 秒自我介绍，重点讲和这个岗位最相关的项目。",
    "讲讲你最近一个 Agent 项目的整体架构和关键取舍。",
    "你怎么设计一个 Agent 的工具调用循环？如何防止死循环？",
]
_counter = {"i": 0}


def tool(fn: Callable) -> Callable:
    if fn.__name__ in TOOLS:
        raise ValueError(f"duplicate tool name: {fn.__name__}")
    TOOLS[fn.__name__] = fn
    return fn


def tool_schemas() -> List[dict]:
    """把注册的工具转成 OpenAI function-calling schema。"""
    out: List[dict] = []
    for name, fn in TOOLS.items():
        props: Dict[str, dict] = {}
        required: List[str] = []
        for pname, p in inspect.signature(fn).parameters.items():
            props[pname] = {"type": "string"}
            if p.default is inspect._empty:
                required.append(pname)
        desc = (fn.__doc__ or "").strip().split("\n")[0]
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            }
        )
    return out


@tool
def next_question() -> str:
    """取下一道面试题（内置题库轮换）。"""
    q = _SEED[_counter["i"] % len(_SEED)]
    _counter["i"] += 1
    return q


@tool
def save_feedback(note: str) -> str:
    """记录一条面试反馈（P0 先只回显，后续落到会话状态）。"""
    return f"saved: {note}"
