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
import uuid
from contextlib import contextmanager
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
        # enabled：总开关，决定 emit 是否真的落盘。
        #   优先级 = 显式传参 > 环境变量 TRACE_ENABLED（默认开）> True。
        #   写文件出错时会被翻成 False（永久静默降级），保证观测不打断面试。
        self.enabled = bool(enabled)
        # scene：本次运行「属于哪个场景」的标签，原样写进每一行的 scene 字段。
        #   由入口处 set_scene() 设置，如 ask:分布式状态机 / interview:bank /
        #   chat / prep / eval:<tag>:<Q1-good>，用来事后把事件按场景切开。
        #   只影响标注，不影响写入行为。
        self.scene = ""
        # seq：本进程内的自增序号，emit 一次 +1，从 1 开始。
        #   作用是把同一进程里的事件排出确定的先后顺序（同 ts 秒级、靠它区分）。
        #   注意：跨进程不连续——每次运行都从 1 重来；要跨运行/跨文件串联，看下面的 request_id。
        self.seq = 0
        # request_id：一次「请求」的标识，由 request() 上下文管理器包出来（如 ask-3f9a2b1c）。
        #   作用：把同一次问答里的 kb / llm / tool / score 四条事件串成一条父链——
        #   面试一场 10 题、每条 4 个事件，没有它就只能靠 ts 猜先后。
        #   用法：入口处 `with get_tracer().request("ask"):`，事件会在窗口内自动带上该 id。
        self.request_id = ""

    # ---- 上下文 ------------------------------------------------------
    def set_scene(self, scene: str) -> None:
        self.scene = scene

    @contextmanager
    def request(self, label: str = "req"):
        """一次请求的事件窗口：进入时生成 request_id，退出时恢复上一个（支持嵌套）。

        目的是让「一道题的一次打分」在日志里成为一条可 grep 的父链：
        request_id + kind 就能把召回、模型、工具、打分四个事件按时间取出来。
        """
        prev = self.request_id
        self.request_id = f"{label}-{uuid.uuid4().hex[:8]}"
        try:
            yield self.request_id
        finally:
            self.request_id = prev

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
            "request_id": self.request_id,
        }
        rec.update(fields)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            # 日志不是关键路径：磁盘只读/无权限时静默降级，不打断面试
            self.enabled = False

    # ---- 四类专用事件 -------------------------------------------------
    # 都是「组装字段 + 调 emit」的薄包装，唯一写入出口仍然是上面的 emit()。
    # 顺序是按一次问答的真实时间轴排的：先召回(kb) → 再调模型(llm) → 中间可能调工具(tool) → 最后打分(score)。
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

    def score(
        self,
        *,
        result: Dict[str, Any],
        reference: Optional[Dict[str, Any]],
        cap_applied: bool,
        cap_reason: str = "",
    ) -> None:
        """打分收口事件：记录「代码最终认下的分数」，不是模型自己说的分数。

        为什么 llm / kb 不够、还要单独一条 score：
          - llm 事件只说明「发了什么、模型回了什么」，但最终分不等于模型输出：
            total 是 app/scoring.py 用六维 × 权重重算的，模型的 total 被丢弃；
            事实不符时还会被 FACT_MISMATCH_CAP 强制封顶。
          - 只看 llm + kb，无法回答「为什么最后是 2.50 而不是 4.20」——中间那段代码裁决没留痕。
          - 这里记的就是裁决结果：六维、重算后的 total、是否触发封顶(cap_applied)、
            以及对照召回要点后的命中/漏掉清单。

        调用点：app/scoring.py 的 score_answer() 末尾（全仓唯一一处），
        所以 CLI(ask) / 整场面试(interview) / Web / 评测(eval) 四条路径都会自动落这条。
        """
        self.emit(
            "score",
            qid=(reference or {}).get("qid"),
            reference_score=(reference or {}).get("score"),
            reference_points=len(((reference or {}).get("points") or [])),
            scores=result.get("scores"),
            total=result.get("total"),
            fact_mismatch=result.get("fact_mismatch"),
            cap_applied=cap_applied,
            cap_reason=cap_reason,
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
