"""单题打分：六维 rubric，加权总分由本地按权重重算（不信模型给的 total）。

对应仓库步骤 6 的思路：把「确定性评分」和「模型判断」分离——
模型只给六维分与依据，总分由代码算，口径可复算。
"""
from __future__ import annotations

from typing import Any, Dict

from .jsonutil import extract_json
from .prompts import SCORING_SYSTEM

DIMENSIONS = ["correctness", "depth", "engineering", "boundary", "structure", "expression"]
DIM_CN = {
    "correctness": "正确性",
    "depth": "深度",
    "engineering": "工程落地",
    "boundary": "边界意识",
    "structure": "结构",
    "expression": "表达",
}
# 事实不符时的总分封顶：事实上讲错的回答不能因为叙述完整而拿到及格线以上的分
FACT_MISMATCH_CAP = 2.0

WEIGHTS = {
    "correctness": 0.30,
    "depth": 0.20,
    "engineering": 0.20,
    "boundary": 0.10,
    "structure": 0.10,
    "expression": 0.10,
}


def _clamp(v: Any, lo: float = 0.0, hi: float = 5.0) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    return max(lo, min(hi, v))


def _ref_block(reference: Dict[str, Any] | None) -> str:
    """把召回要点拼成 prompt 片段；没有召回就返回空串（退化回 P2 行为）。"""
    if not reference:
        return ""
    pts = reference.get("points") or []
    lines = [f"\n\n【参考要点（本人最佳答案，仅作覆盖度对照）】", f"题目标签：{reference.get('topic','')}"]
    for i, p in enumerate(pts[:6], 1):
        lines.append(f"{i}. {str(p.get('text', ''))[:100]}")
    if reference.get("numbers"):
        lines.append(f"关键数字：{str(reference['numbers'])[:100]}")
    if reference.get("boundary"):
        lines.append(f"容易说过的边界：{str(reference['boundary'])[:100]}")
    return "\n".join(lines)


def _norm_list(v: Any, points: List[Dict[str, Any]]) -> List[str]:
    """把模型返回的「编号 / 短标签」统一成可读标签。"""
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for x in v:
        s = str(x).strip()
        if not s:
            continue
        if s.isdigit() and 1 <= int(s) <= len(points):
            out.append(points[int(s) - 1].get("label", s))
        else:
            out.append(s)
    return out


def score_answer(
    llm: Any,
    question: str,
    answer: str,
    role: str = "AI Agent / 后端",
    reference: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """reference 是 kb.retrieve() 的结果；只用于对照覆盖度，不额外增加调用次数。"""
    user = (
        f"岗位方向：{role}\n\n"
        f"【面试问题】\n{question}\n\n"
        f"【候选人回答】\n{answer}"
        f"{_ref_block(reference)}\n\n"
        f"请按六维 rubric 打分，并给出 covered / missing 对照结果，输出 JSON。"
    )
    resp = llm.chat([
        {"role": "system", "content": SCORING_SYSTEM},
        {"role": "user", "content": user},
    ])
    raw = resp.choices[0].message.content
    data = extract_json(raw)

    scores = {d: _clamp(data.get(d, 0)) for d in DIMENSIONS}
    total = round(sum(scores[d] * WEIGHTS[d] for d in DIMENSIONS), 2)

    # 事实不符（回答与本人项目事实对不上）在代码端封顶：这类回答无论叙述多完整都不能当高分用。
    fact_mismatch = bool(data.get("fact_mismatch", False))
    cap_applied = False
    if fact_mismatch and total > FACT_MISMATCH_CAP:
        total = FACT_MISMATCH_CAP
        cap_applied = True

    points = (reference or {}).get("points") or []
    covered = _norm_list(data.get("covered", []), points)
    missing = _norm_list(data.get("missing", []), points)

    result = {
        "question": question,
        "answer": answer,
        "scores": scores,
        "total": total,
        "evidence": data.get("evidence", []),
        "summary": data.get("summary", ""),
        "strengths": data.get("strengths", []),
        "weaknesses": data.get("weaknesses", []),
        "fact_mismatch": fact_mismatch,
        "covered": covered,
        "missing": missing,
        "reference": {
            "qid": (reference or {}).get("qid", ""),
            "topic": (reference or {}).get("topic", ""),
            "score": (reference or {}).get("score", 0),
            "points": [p.get("text", "") for p in points],
            "numbers": (reference or {}).get("numbers", ""),
        } if reference else None,
    }

    try:  # 落一条 score 事件：把「为什么给这个分」所需的三段都留痕
        from .trace import get_tracer

        get_tracer().score(result=result, reference=reference, cap_applied=cap_applied)
    except Exception:
        pass
    return result
