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


def score_answer(llm: Any, question: str, answer: str, role: str = "AI Agent / 后端") -> Dict[str, Any]:
    user = (
        f"岗位方向：{role}\n\n"
        f"【面试问题】\n{question}\n\n"
        f"【候选人回答】\n{answer}\n\n"
        f"请按六维 rubric 打分并输出 JSON。"
    )
    resp = llm.chat([
        {"role": "system", "content": SCORING_SYSTEM},
        {"role": "user", "content": user},
    ])
    raw = resp.choices[0].message.content
    data = extract_json(raw)

    scores = {d: _clamp(data.get(d, 0)) for d in DIMENSIONS}
    total = round(sum(scores[d] * WEIGHTS[d] for d in DIMENSIONS), 2)

    return {
        "question": question,
        "answer": answer,
        "scores": scores,
        "total": total,
        "evidence": data.get("evidence", []),
        "summary": data.get("summary", ""),
        "strengths": data.get("strengths", []),
        "weaknesses": data.get("weaknesses", []),
    }
