"""M1 题库生成：JD + 简历 → 结构化题库。"""
from __future__ import annotations

from typing import Any, Dict

from .jsonutil import extract_json
from .prompts import QUESTION_GEN_SYSTEM


def generate_bank(llm: Any, jd: str, resume: str, n: int = 10, role: str = "AI Agent / 后端") -> Dict[str, Any]:
    user = (
        f"岗位方向：{role}\n目标题量：约 {n} 题\n\n"
        f"【JD】\n{jd}\n\n"
        f"【简历 / 项目材料】\n{resume}\n\n"
        f"请生成题库 JSON。"
    )
    resp = llm.chat([
        {"role": "system", "content": QUESTION_GEN_SYSTEM},
        {"role": "user", "content": user},
    ])
    raw = resp.choices[0].message.content
    data = extract_json(raw)

    if isinstance(data, list):
        qs = data
    else:
        qs = data.get("questions", [])

    out = []
    for i, q in enumerate(qs, 1):
        out.append({
            "id": q.get("id", f"Q{i}"),
            "category": q.get("category", "技术"),
            "topic": q.get("topic", ""),
            "question": q.get("question", ""),
            "points": q.get("points", []) or [],
            "followups": q.get("followups", []) or [],
        })
    return {"role": role, "count": len(out), "questions": out}
