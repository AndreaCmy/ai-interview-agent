"""把题库 / 单题评分 / 整场报告渲染成 Markdown（可直接给 Markor 用）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .scoring import DIM_CN, DIMENSIONS


def bank_to_markdown(bank: Dict[str, Any]) -> str:
    lines = [f"# 题库（{bank.get('count', len(bank.get('questions', [])))} 题）", ""]
    for q in bank.get("questions", []):
        lines.append(f"## {q['id']} · [{q['category']}] {q['topic']}")
        lines.append("")
        lines.append(q["question"])
        if q.get("points"):
            lines.append("")
            lines.append("**考察点**：" + "；".join(q["points"]))
        if q.get("followups"):
            lines.append("**追问链**：" + "；".join(q["followups"]))
        lines.append("")
    return "\n".join(lines)


def score_to_markdown(item: Dict[str, Any]) -> str:
    s = item["scores"]
    lines = ["## 评分卡", ""]
    lines.append("| 维度 | 分数 |")
    lines.append("| --- | --- |")
    for d in DIMENSIONS:
        lines.append(f"| {DIM_CN[d]} | {s.get(d, 0):.1f} |")
    lines.append(f"| **加权总分** | **{item['total']:.2f}** |")
    lines.append("")
    if item.get("summary"):
        lines.append(f"**总评**：{item['summary']}")
        lines.append("")
    if item.get("evidence"):
        lines.append("**依据（引自你的回答）**：")
        for e in item["evidence"]:
            lines.append(f"- {e}")
        lines.append("")
    if item.get("covered"):
        lines.append("**命中要点**：" + "；".join(str(x) for x in item["covered"]))
    if item.get("missing"):
        lines.append("**漏掉的要点**：" + "；".join(str(x) for x in item["missing"]))
    ref = item.get("reference") or {}
    if ref.get("topic"):
        lines.append(f"**对照依据**：{ref['topic']}（{ref.get('qid','')}，检索分 {ref.get('score','')}）")
    if item.get("strengths"):
        lines.append("**亮点**：" + "；".join(str(x) for x in item["strengths"]))
    if item.get("weaknesses"):
        lines.append("**改进**：" + "；".join(str(x) for x in item["weaknesses"]))
    return "\n".join(lines)


def session_report_markdown(
    bank: Dict[str, Any],
    turns: List[Dict[str, Any]],
    report_data: Optional[Dict[str, Any]] = None,
) -> str:
    lines = ["# 模拟面试报告", ""]

    # 1) 总览
    lines += ["## 总览", "", "| # | 主题 | 加权总分 |", "| --- | --- | --- |"]
    for i, t in enumerate(turns, 1):
        lines.append(f"| {t.get('id', i)} | {t.get('topic', '')} | {t.get('total', 0):.2f} |")
    if turns:
        avg = sum(t.get("total", 0) for t in turns) / len(turns)
        lines += ["", f"**平均分**：{avg:.2f}（{len(turns)} 题）"]
    lines.append("")

    # 2) 复习优先级：优先用模型给的，否则按分数升序（最弱先）
    lines += ["## 复习优先级", ""]
    prio = None
    if report_data and report_data.get("priority"):
        prio = report_data["priority"]
    if prio:
        for n, p in enumerate(prio, 1):
            lines.append(f"{n}. **{p.get('id','')}** — {p.get('reason','')}")
    else:
        weakest = sorted(turns, key=lambda x: x.get("total", 0))
        for n, t in enumerate(weakest, 1):
            lines.append(f"{n}. **{t.get('id', n)}** {t.get('topic','')} — 得分 {t.get('total', 0):.2f}（越靠前越该先补）")
    lines.append("")

    # 3) 逐题复盘
    lines += ["## 逐题复盘", ""]
    per_q = {}
    if report_data:
        for p in report_data.get("per_question", []):
            per_q[p.get("id", "")] = p

    for t in turns:
        qid = t.get("id", "")
        lines.append(f"### {qid} · {t.get('topic','')}（{t.get('total', 0):.2f}）")
        lines.append("")
        lines.append(f"**题目**：{t.get('question','')}")
        lines.append("")
        lines.append(f"**你的回答**：{t.get('answer','')}")
        if t.get("fact_mismatch"):
            lines.append("")
            lines.append("**事实不符**：这条回答与本人项目事实对不上（见改进项），总分已按规则封顶。")
        lines.append("")
        if t.get("covered") or t.get("missing"):
            ref = t.get("reference") or {}
            tag = f"（对照 {ref['qid']}）" if ref.get("qid") else ""
            if t.get("covered"):
                lines.append(f"**命中要点{tag}**：" + "；".join(str(x) for x in t["covered"]))
            if t.get("missing"):
                lines.append(f"**漏掉的要点{tag}**：" + "；".join(str(x) for x in t["missing"]))
            lines.append("")
        p = per_q.get(qid)
        if p and p.get("improved_answer"):
            lines.append("**改进话术（可背诵）**：")
            lines.append("")
            lines.append(p["improved_answer"])
            lines.append("")
            if p.get("key_points"):
                lines.append("**要补的关键点**：" + "；".join(str(x) for x in p["key_points"]))
                lines.append("")
        else:
            lines.append("**待补要点**：" + ("；".join(str(x) for x in t.get("weaknesses", [])) or "（无）"))
            lines.append("")

    if report_data and report_data.get("overall"):
        lines += ["## 整场总评", "", report_data["overall"], ""]
    else:
        lines += ["## 说明", "", "本次未生成逐题改进话术（可能因 LLM 调用预算用尽）。可加大 `--max-calls` 后重跑。", ""]

    return "\n".join(lines)
