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
import json
import types
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union, get_args, get_origin, get_type_hints

TOOLS: Dict[str, Callable] = {}

# 参数值域约束：(工具名, 参数名) → JSON Schema 关键字。
# 为什么单独一张表而不是从注解推：注解只能表达「类型」，表达不了「这个字符串 1–200 字」
# 「这个 int 只能是 1–20」。范围是业务口径，写进 schema 才是硬约束——模型看得到边界，
# pydantic 也照同一份约束拒绝越界值，不会出现「说明书说可以、执行时才炸」。
PARAM_LIMITS: Dict[Tuple[str, str], Dict[str, Any]] = {
    ("save_feedback", "note"): {"minLength": 1, "maxLength": 200},
}

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
        # 值域约束：模型看到 minimum/maximum（或 minLength/maxLength）才知道边界在哪
        prop.update(PARAM_LIMITS.get((name or fn.__name__, pname), {}))
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


# ---------------------------------------------------------------- 执行前校验
_PY_TYPES = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}
_MODEL_CACHE: Dict[str, Any] = {}


def _py_type(prop: Dict[str, Any]):
    """把 schema 里的 JSON 类型还原成 Python 类型，供 pydantic 建模型。"""
    if "enum" in prop:
        # 枚举用 Literal 表达，pydantic 会直接拒掉不在枚举里的值
        return Literal[tuple(prop["enum"])]  # type: ignore[valid-type]
    return _PY_TYPES.get(prop.get("type", "string"), str)


def _args_model(name: str, schema: dict):
    """按 schema 动态建一个 pydantic 模型，按工具名缓存，避免每次调用都重建。"""
    if name in _MODEL_CACHE:
        return _MODEL_CACHE[name]
    from pydantic import ConfigDict, Field, create_model

    fn = schema["function"]
    params = fn.get("parameters", {}) or {}
    props: Dict[str, Any] = params.get("properties", {}) or {}
    required = set(params.get("required", []) or [])

    fields: Dict[str, Any] = {}
    for pname, prop in props.items():
        py_t = _py_type(prop)
        # schema 里的值域关键字 → pydantic 的字段约束：说明书写什么，守门就按什么拦
        limits = {
            "ge": prop.get("minimum"),
            "le": prop.get("maximum"),
            "min_length": prop.get("minLength"),
            "max_length": prop.get("maxLength"),
        }
        limits = {k: v for k, v in limits.items() if v is not None}
        if pname in required:
            fields[pname] = (py_t, Field(..., **limits))
        else:
            # 不必填的参数给 None 默认值，模型少给字段也能过
            fields[pname] = (Optional[py_t], Field(default=None, **limits))
    # extra="forbid": 模型多塞一个 schema 里没有的参数也算不合法——
    # 否则拼错的字段会被静默丢掉，函数用自己的默认值跑，错得无声无息
    model = create_model(f"{name}_args", __config__=ConfigDict(extra="forbid"), **fields)  # type: ignore[call-overload]
    _MODEL_CACHE[name] = model
    return model


def validate_args(schemas: List[dict], name: str, raw: str) -> Dict[str, Any]:
    """执行前校验工具参数：模型给的 JSON → 校验/转型/补默认 → 字典。

    这就是「schema 管说明书，校验管守门」的那道门：
    schema 让模型有意愿写对，这里保证写错了进不来（比如 top_k 传 -1、枚举传不存在的值）。
    校验失败抛 ValueError，由 agent 循环转成 `error: ...` 回喂给模型，让它自己改。
    没装 pydantic 时退化成「只解析 JSON」，不影响主流程。
    """
    schema = next((s for s in schemas if s.get("function", {}).get("name") == name), None)
    if schema is None:
        raise ValueError(f"unknown tool: {name}")
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"arguments 不是合法 JSON: {e}") from None
    if not isinstance(data, dict):
        raise ValueError("arguments 必须是 JSON 对象")

    try:
        model = _args_model(name, schema)
    except Exception:
        return data  # 没装 pydantic：放行，由函数自身报错
    from pydantic import ValidationError

    try:
        # 去掉值为 None 的字段：否则会把 None 传给函数，覆盖掉函数自己的默认值
        return {k: v for k, v in model.model_validate(data).model_dump().items() if v is not None}
    except ValidationError as e:
        # 只报第一条错误，够模型看懂；整段嵌套报错会把它绕晕
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc", ())) or "参数"
        raise ValueError(f"参数不合法 {loc}: {first.get('msg', '')}") from None


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
