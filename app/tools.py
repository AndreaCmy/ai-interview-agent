"""工具注册表 + schema 生成。

对照仓库步骤 1 的 `@tool` 装饰器 + `inspect.signature` 生成 schema，
这里改成生成 OpenAI 兼容的 `{"type":"function","function":{...}}` 格式。

P3 补的第一版局限：schema 里所有参数一律 `{"type":"string"}`，模型只能靠描述去猜
int / 枚举 / 可选，参数一多必然调错。现在改成从类型注解推导：

- `int → integer`、`float → number`、`bool → boolean`、`list[str] → array[string]`
- `Literal["a","b"] → enum`（枚举值直接告诉模型可选范围）
- `Optional[T]` / 有默认值 → 不进 `required`，模型知道可以不填
- 完全没有注解时退回 `string`，与旧行为兼容，不会因为一个人没写注解就炸掉整个 schema
"""
from __future__ import annotations

import inspect
import types
from typing import Any, Callable, Dict, List, Literal, Union, get_args, get_origin, get_type_hints

TOOLS: Dict[str, Callable] = {}

# 极简题库（P0 先用内嵌题；后续接 interview-qa-standard.md 做检索）
_SEED = [
    "请做一个 60 秒自我介绍，重点讲和这个岗位最相关的项目。",
    "讲讲你最近一个 Agent 项目的整体架构和关键取舍。",
    "你怎么设计一个 Agent 的工具调用循环？如何防止死循环？",
]
_counter = {"i": 0}

# 基础类型 → JSON Schema 类型。Python 的 str/int/float/bool 本身就是类型对象，可以直接查表。
_SIMPLE_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def _json_type(ann: Any) -> Dict[str, Any]:
    """把一个类型注解转成 JSON Schema 片段；拆不出来就退化为 string。"""
    if ann is inspect._empty or ann is None or ann is Any:
        return {"type": "string"}  # 无注解：保持旧行为，不让 schema 生成失败

    origin = get_origin(ann)
    args = get_args(ann)

    # Optional[X] 与 X | None 都是 Union；剥掉 None 后按内层类型描述
    if origin in (Union, types.UnionType):
        inner = [a for a in args if a is not type(None)]
        return _json_type(inner[0]) if len(inner) == 1 else {"type": "string"}

    # Literal["easy","hard"] → 枚举，模型不用猜合法取值
    if origin is Literal:
        values = list(args)
        enum_type = "string" if all(isinstance(v, str) for v in values) else "integer"
        return {"type": enum_type, "enum": values}

    # list[str] / list[int] → 数组，带 items
    if origin in (list, List):
        item_t = _json_type(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item_t}

    # dict[str, Any] 之类 → object（不细化 properties，避免递归过深）
    if origin in (dict, Dict):
        return {"type": "object"}

    return {"type": _SIMPLE_TYPES.get(ann, "string")}


def _param_doc(fn: Callable) -> Dict[str, str]:
    """从 docstring 的 Args 段落里取每个参数的一句话说明。

    只在有 `Args:`（或 `参数:`）段时解析，格式 `name: 说明` 或 `name：说明`。
    取不到就返回空 dict，description 留空也不影响调用。
    """
    doc = inspect.getdoc(fn) or ""
    out: Dict[str, str] = {}
    in_args = False
    for raw in doc.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.rstrip(":") in ("Args", "Arguments", "参数"):
            in_args = True
            continue
        if in_args:
            for sep in (":", "："):
                if sep in line:
                    key, val = line.split(sep, 1)
                    key = key.strip().lstrip("*")
                    if key and " " not in key:
                        out[key] = val.strip()
                    break
            else:
                # 没有冒号说明这一行不是参数行，Args 段结束
                in_args = False
    return out


def schema_for(fn: Callable, name: str | None = None) -> dict:
    """为单个函数生成 OpenAI function schema（工具与测试共用同一入口）。"""
    # 本模块开了 `from __future__ import annotations`，签名里的注解是字符串；
    # 必须用 get_type_hints 还原成真正的类型对象，否则永远只能匹配到 string。
    try:
        hints = get_type_hints(fn)
    except Exception:  # 前向引用/无法解析时退化成原始注解，不影响生成
        hints = {}
    doc = inspect.getdoc(fn) or ""
    desc = doc.strip().split("\n")[0] if doc else ""
    param_docs = _param_doc(fn)
    props: Dict[str, Any] = {}
    required: List[str] = []

    for pname, p in inspect.signature(fn).parameters.items():
        ann = hints.get(pname, p.annotation)
        prop = _json_type(ann)
        if pname in param_docs:
            prop["description"] = param_docs[pname]
        props[pname] = prop
        # 有默认值等于「可以不填」；Optional[X] 同理，别逼模型给 null
        if p.default is inspect._empty and not _is_optional(ann):
            required.append(pname)

    return {
        "type": "function",
        "function": {
            "name": name or fn.__name__,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def _is_optional(ann: Any) -> bool:
    origin = get_origin(ann)
    if origin not in (Union, types.UnionType):
        return False
    return any(a is type(None) for a in get_args(ann))


def tool(fn: Callable) -> Callable:
    if fn.__name__ in TOOLS:
        raise ValueError(f"duplicate tool name: {fn.__name__}")
    TOOLS[fn.__name__] = fn
    return fn


def tool_schemas() -> List[dict]:
    """把注册的工具转成 OpenAI function-calling schema。"""
    return [schema_for(fn, name) for name, fn in TOOLS.items()]


@tool
def next_question() -> str:
    """取下一道面试题（内置题库轮换）。"""
    q = _SEED[_counter["i"] % len(_SEED)]
    _counter["i"] += 1
    return q


@tool
def save_feedback(note: str) -> str:
    """记录一条面试反馈（P0 先只回显，后续落到会话状态）。

    Args:
        note: 反馈内容，一句话即可
    """
    return f"saved: {note}"
