"""可观测：把每次 LLM 调用、工具调用、召回与打分落成一行 JSONL。

为什么要有这个：打分是「模型判断 + 代码重算」两段拼出来的，光看最终分数无法回答
「这道题为什么给这个分」。trace 把三段都留痕 —— 发了什么、召回了哪些要点、
模型给了什么、代码怎么重算与封顶 —— 于是任何一条分数都能倒查回去。

隐私：默认写 `my/trace.jsonl`（`my/` 已在 .gitignore 里），不落完整 prompt，
只落「角色 + 字符数 + 前 80 字」用于核对，避免简历原文整段进日志。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PATH = "my/trace.jsonl"
_HEAD = 80


def _head(text: Any, n: int = _HEAD) -> str:
    s = str(text or "").replace("\n", " ").strip()
    return s[:n]


class Tracer:
    """极简 JSONL 追加器：写失败不影响主流程。"""

    def __init__(self, path: Optional[str] = None, enabled: Optional[bool] = None) -> None:
        env_path = os.getenv("TRACE_PATH", "")
        if enabled is None:
            enabled = os.getenv("TRACE_ENABLED", "1") not in ("0", "false", "False")
        self.path = Path(path or env_path or DEFAULT_PATH)
        self.enabled = bool(enabled)
        self.scene = ""
        self.seq = 0

    # ---- 上下文 ------------------------------------------------------
    def set_scene(self, scene: str) -> None:
        self.scene = scene

    # ---- 写入 --------------------------------------------------------
    def emit(self, kind: str, **fields: Any) -> None:
        if not self.enabled:
            return
        self.seq += 1
        rec: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "seq": self.seq,
            "kind": kind,
            "scene": self.scene,
        }
        rec.update(fields)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            # 日志不是关键路径：磁盘只读/无权限时静默降级，不打断面试
            self.enabled = False

    # ---- 三类专用事件 -------------------------------------------------
    def llm_call(
        self,
        *,
        model: str,
        temperature: Any,
        messages: List[dict],
        tools: Optional[List[dict]],
        latency_ms: int,
        ok: bool,
        response: Any = None,
        error: str = "",
        call_no: int = 0,
    ) -> None:
        self.emit(
            "llm",
            model=model,
            temperature=temperature,
            call_no=call_no,
            latency_ms=latency_ms,
            ok=ok,
            error=_head(error, 200),
            messages=[{"role": m.get("role"), "chars": len(str(m.get("content") or ""))} for m in messages],
            prompt_chars=sum(len(str(m.get("content") or "")) for m in messages),
            tools=[t.get("function", {}).get("name") for t in (tools or [])],
            response_chars=len(str(response or "")),
            response_head=_head(response),
        )

    def tool_call(self, *, name: str, args: Any, latency_ms: int, ok: bool, result: Any = "", error: str = "") -> None:
        self.emit(
            "tool",
            name=name,
            args=_head(args),
            latency_ms=latency_ms,
            ok=ok,
            error=_head(error, 200),
            result_head=_head(result),
        )

    def kb_hit(self, *, question: str, hit: Optional[Dict[str, Any]], min_score: float) -> None:
        self.emit(
            "kb",
            question_head=_head(question),
            min_score=min_score,
            hit=bool(hit),
            qid=(hit or {}).get("qid"),
            score=(hit or {}).get("score"),
            points=len(((hit or {}).get("points") or [])),
        )

    def score(self, *, result: Dict[str, Any], reference: Optional[Dict[str, Any]], cap_applied: bool) -> None:
        self.emit(
            "score",
            qid=(reference or {}).get("qid"),
            reference_score=(reference or {}).get("score"),
            reference_points=len(((reference or {}).get("points") or [])),
            scores=result.get("scores"),
            total=result.get("total"),
            fact_mismatch=result.get("fact_mismatch"),
            cap_applied=cap_applied,
            covered=result.get("covered"),
            missing=result.get("missing"),
        )


_TRACER: Optional[Tracer] = None


def get_tracer() -> Tracer:
    global _TRACER
    if _TRACER is None:
        _TRACER = Tracer()
    return _TRACER


def configure(path: Optional[str] = None, enabled: Optional[bool] = None) -> Tracer:
    """命令行/评测入口用这个改路径或关掉（如纯离线单测）。"""
    global _TRACER
    _TRACER = Tracer(path=path, enabled=enabled)
    return _TRACER


def set_scene(scene: str) -> None:
    get_tracer().set_scene(scene)
