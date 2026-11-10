"""P2 模拟面试编排（模式 A：终端一问一答）。

LLM 调用预算：每答一题 1 次打分；追问（可选）再 1 次；最后复盘报告 1 次。
超出预算时：得分已记录，停止继续出题 / 报告降级为本地确定性版。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from .budget import BudgetExceeded
from .jsonutil import extract_json
from .prompts import REPORT_SYSTEM
from .report import score_to_markdown
from .scoring import score_answer
from .session import SessionStore
from .trace import get_tracer


def run_interview(
    llm: Any,
    bank: Dict[str, Any],
    role: str,
    store: SessionStore,
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
    followup: bool = False,
    max_followups: int = 0,
    kb: Any = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    sid = store.create_session(role, bank)
    turns: List[Dict[str, Any]] = []
    questions = bank.get("questions", [])
    seq = 0
    total_q = len(questions)

    def retrieve(q: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if kb is None:
            return None
        try:
            return kb.retrieve(q.get("question", ""), q.get("topic", ""))
        except Exception:
            return None  # 知识库异常不影响面试主流程

    for i, q in enumerate(questions, 1):
        out(f"\n=== 第 {i}/{total_q} 题 · [{q.get('category','技术')}] {q.get('topic','')} ===")
        out(q.get("question", ""))
        answer = input_fn("你的回答（直接回车跳过）> ")
        if not answer or not answer.strip():
            out("（已跳过本题，不计分）")
            continue

        # 每题一个 request 窗口：这道题的 kb / llm / score 事件共用一个 request_id，
        # 事后 grep 一条就能拿到整题的来龙去脉
        with get_tracer().request(label=q.get("id", f"Q{i}")):
            ref = retrieve(q)
            try:
                item = score_answer(llm, q.get("question", ""), answer, role=role, reference=ref)
            except BudgetExceeded as e:
                out(f"\n[预算用尽] {e} 停止继续出题，已记录的题目仍会进报告。")
                break

        item["id"] = q.get("id", f"Q{i}")
        item["qid"] = item["id"]  # 会话表另有 qid 列，记录题目编号，回放时能对上题库
        item["topic"] = q.get("topic", "")
        item["category"] = q.get("category", "技术")
        seq += 1
        store.add_turn(sid, seq, item)
        turns.append(item)
        out("")
        out(score_to_markdown(item))

        if followup and max_followups > 0 and item["total"] < 3.0 and q.get("followups"):
            fu = q["followups"][0]
            out(f"\n追问：{fu}")
            fa = input_fn("追问回答（回车跳过）> ")
            if fa and fa.strip():
                with get_tracer().request(label=f"FU{i}"):
                    try:
                        fi = score_answer(llm, fu, fa, role=role, reference=retrieve({"question": fu}))
                    except BudgetExceeded as e:
                        out(f"\n[预算用尽] {e}")
                        break
                fi["id"] = q.get("id", f"Q{i}") + "-追问"
                fi["qid"] = fi["id"]
                fi["topic"] = q.get("topic", "")
                fi["category"] = "追问"
                seq += 1
                store.add_turn(sid, seq, fi)
                turns.append(fi)
                max_followups -= 1
                out("")
                out(score_to_markdown(fi))

    store.finish(sid)
    return sid, turns


def generate_report(llm: Any, bank: Dict[str, Any], turns: List[Dict[str, Any]], role: str) -> Optional[Dict[str, Any]]:
    """一次调用生成「逐题改进话术 + 复习优先级」；预算不足返回 None（调用方降级）。"""
    if not turns:
        return None
    payload = []
    for t in turns:
        ref = t.get("reference") or {}
        payload.append({
            "id": t.get("id", ""),
            "topic": t.get("topic", ""),
            "question": t.get("question", ""),
            "answer": t.get("answer", ""),
            "total": t.get("total", 0),
            "weaknesses": t.get("weaknesses", []),
            "covered": t.get("covered", []),
            "missing": t.get("missing", []),
            "reference_points": ref.get("points", []),
            "reference_numbers": ref.get("numbers", ""),
        })
    user = (
        f"岗位方向：{role}\n\n"
        f"【整场问答与评分】\n"
        + "\n".join(
            f"- {p['id']} [{p['topic']}] 得分{p['total']}；问题：{p['question']}；"
            f"回答：{p['answer']}；不足：{'；'.join(str(x) for x in p['weaknesses'])}"
            + (f"；命中要点：{'；'.join(str(x) for x in p['covered'])}" if p["covered"] else "")
            + (f"；漏掉要点：{'；'.join(str(x) for x in p['missing'])}" if p["missing"] else "")
            + (f"\n  【参考要点】{'｜'.join(str(x)[:80] for x in p['reference_points'][:6])}"
               + (f"（关键数字：{p['reference_numbers']}）" if p["reference_numbers"] else "")
               if p["reference_points"] else "")
            for p in payload
        )
        + "\n\n请输出复盘 JSON。"
    )
    try:
        resp = llm.chat([
            {"role": "system", "content": REPORT_SYSTEM},
            {"role": "user", "content": user},
        ])
    except BudgetExceeded:
        return None
    try:
        return extract_json(resp.choices[0].message.content)
    except Exception:
        return None
