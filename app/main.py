"""CLI 入口。

用法：
  python -m app demo                         # 无 key，stub 跑通循环+工具调度
  python -m app chat "开始面试"               # 真实 DeepSeek 跑一轮
  python -m app prep --jd jd.txt --resume resume.txt -o bank.md --json bank.json
  python -m app ask --question "..." --answer "..."
  python -m app interview --bank bank.json --max-calls 12 -o report.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .llm import RealLLM, StubLLM
from .tools import TOOLS, tool_schemas
from .agent import run_agent
from .prep import generate_bank
from .scoring import score_answer
from .report import bank_to_markdown, score_to_markdown, session_report_markdown
from .budget import BudgetedLLM, BudgetExceeded
from .session import SessionStore
from .interview import run_interview, generate_report
from .kb import DEFAULT_KB, KnowledgeBase
from .trace import configure as configure_trace, set_scene

SYSTEM = (
    "你是一位经验丰富的技术面试官，风格专业、克制。"
    "你会按主题出题并根据候选人的回答追问；每轮只做一件事。"
    "需要出题时调用 next_question 工具；收尾时给出简短评价。"
)


def _make_real() -> RealLLM | None:
    try:
        return RealLLM()
    except RuntimeError as e:
        print(f"[配置错误] {e}")
        return None


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _wrap_api(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "Insufficient Balance" in msg or " 402" in msg or "code: 402" in msg:
            print("[余额不足 402] DeepSeek 账户余额不足，请到 platform.deepseek.com 充值后重试。")
        else:
            print(f"[调用失败] {e}")
        return None


def cmd_demo(args: argparse.Namespace) -> int:
    llm = StubLLM()
    print("== P0 演示（stub LLM，无需 API Key）==")
    res = run_agent(llm, SYSTEM, "开始面试", tool_schemas(), TOOLS, max_iter=6, verbose=True)
    print("\n--- 结果 ---")
    print("迭代次数:", res["iterations"])
    print("最终回答:", res["final"])
    print("消息条数:", len(res["messages"]))
    ok = res["final"] and any(m.get("role") == "tool" for m in res["messages"])
    print("\n[验证]", "PASS 循环+工具调度跑通" if ok else "FAIL 未完成工具调用")
    return 0 if ok else 1


def cmd_chat(args: argparse.Namespace) -> int:
    llm = _make_real()
    if not llm:
        return 2
    configure_trace(getattr(args, "trace", None), enabled=not getattr(args, "no_trace", False))
    set_scene("chat")
    budget = BudgetedLLM(llm, 8, label="LLM")
    res = _wrap_api(lambda: run_agent(budget, SYSTEM, args.message, tool_schemas(), TOOLS, max_iter=8, verbose=True))
    if not res:
        return 1
    print("\n--- 最终回答 ---")
    print(res["final"])
    return 0


def cmd_prep(args: argparse.Namespace) -> int:
    llm = _make_real()
    if not llm:
        return 2
    configure_trace(getattr(args, "trace", None), enabled=not getattr(args, "no_trace", False))
    set_scene("prep")
    budget = BudgetedLLM(llm, 1, label="LLM")
    jd = _read_text(args.jd)
    resume = _read_text(args.resume)
    bank = _wrap_api(lambda: generate_bank(budget, jd, resume, n=args.n, role=args.role))
    if bank is None:
        return 1
    md = bank_to_markdown(bank)
    print(md)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"\n[已写入] {args.out}")
    if args.json:
        Path(args.json).write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[已写入] {args.json}")
    return 0


def _make_kb(path: str | None):
    kb = KnowledgeBase(path or DEFAULT_KB)
    print(f"[知识库] {kb.path} | 段落 {len(kb.sections)}")
    return kb


def cmd_ask(args: argparse.Namespace) -> int:
    llm = _make_real()
    if not llm:
        return 2
    configure_trace(getattr(args, "trace", None), enabled=not getattr(args, "no_trace", False))
    set_scene(f"ask:{args.topic or args.question[:20]}")
    kb = _make_kb(getattr(args, "kb", None))
    ref = kb.retrieve(args.question, args.topic or "")
    if ref:
        print(f"[召回] {ref['qid']} · {ref['topic']}（分 {ref['score']}）")
    budget = BudgetedLLM(llm, 1, label="LLM")
    item = _wrap_api(lambda: score_answer(budget, args.question, args.answer, role=args.role, reference=ref))
    if item is None:
        return 1
    md = score_to_markdown(item)
    print("\n## 题目\n\n" + args.question + "\n\n" + md)
    if args.out:
        Path(args.out).write_text("## 题目\n\n" + args.question + "\n\n" + md, encoding="utf-8")
        print(f"\n[已写入] {args.out}")
    return 0


def cmd_interview(args: argparse.Namespace) -> int:
    llm = _make_real()
    if not llm:
        return 2
    configure_trace(getattr(args, "trace", None), enabled=not getattr(args, "no_trace", False))
    set_scene(f"interview:{Path(args.bank).stem}")
    bank = json.loads(Path(args.bank).read_text(encoding="utf-8"))
    qs = bank.get("questions", [])
    if not qs:
        print("[错误] 题库为空")
        return 1

    max_calls = args.max_calls
    if max_calls is None:
        max_calls = len(qs) + 1 + (len(qs) if args.followup else 0)
    budget = BudgetedLLM(llm, max_calls, label="LLM")

    store = SessionStore(args.db)
    kb = _make_kb(getattr(args, "kb", None))
    sid, turns = run_interview(
        budget, bank, args.role, store,
        input_fn=input, out=print,
        followup=args.followup, max_followups=args.max_followups,
        kb=kb,
    )
    report_data = generate_report(budget, bank, turns, args.role)
    md = session_report_markdown(bank, turns, report_data)
    print("\n" + md)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"\n[已写入] {args.out}")
    print(f"\n[会话] #{sid} | 记录 {len(turns)} 题 | LLM 调用 {budget.calls}/{max_calls}")
    store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai-interview-agent", description="AI 面试 Agent")
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("demo", help="用 stub LLM 跑通循环与工具调度（无需 key）")
    d.set_defaults(func=cmd_demo)

    c = sub.add_parser("chat", help="用真实 DeepSeek 跑一轮")
    c.add_argument("message", nargs="?", default="开始面试", help="用户消息")
    c.set_defaults(func=cmd_chat)

    pr = sub.add_parser("prep", help="JD+简历 → 题库")
    pr.add_argument("--jd", required=True, help="JD 文本文件路径，或 - 表示 stdin")
    pr.add_argument("--resume", required=True, help="简历/项目材料文件路径，或 - 表示 stdin")
    pr.add_argument("--n", type=int, default=10, help="目标题量")
    pr.add_argument("--role", default="AI Agent / 后端", help="岗位方向")
    pr.add_argument("-o", "--out", help="题库 Markdown 输出路径")
    pr.add_argument("--json", help="题库 JSON 输出路径")
    pr.set_defaults(func=cmd_prep)

    a = sub.add_parser("ask", help="单题打分")
    a.add_argument("--question", required=True, help="面试题")
    a.add_argument("--answer", required=True, help="候选人回答")
    a.add_argument("--role", default="AI Agent / 后端", help="岗位方向")
    a.add_argument("--topic", default="", help="题目主题（帮助检索命中对应要点）")
    a.add_argument("--kb", default=None, help="对照要点库路径（默认 my/best-answers.md）")
    a.add_argument("--trace", default=None, help="trace JSONL 路径（默认 my/trace.jsonl）")
    a.add_argument("--no-trace", action="store_true", help="本次不写 trace")
    a.add_argument("-o", "--out", help="评分卡 Markdown 输出路径")
    a.set_defaults(func=cmd_ask)

    iv = sub.add_parser("interview", help="完整一场模拟面试（模式 A：一问一答）")
    iv.add_argument("--bank", required=True, help="题库 JSON 路径（prep --json 产出）")
    iv.add_argument("--role", default="AI Agent / 后端", help="岗位方向")
    iv.add_argument("--max-calls", type=int, default=None, help="LLM 调用上限（默认 题数+1）")
    iv.add_argument("--followup", action="store_true", help="对低分题自动追问（每追问 +1 次调用）")
    iv.add_argument("--max-followups", type=int, default=2, help="最多追问次数")
    iv.add_argument("--db", default="interview.db", help="SQLite 数据库路径")
    iv.add_argument("--kb", default=None, help="对照要点库路径（默认 my/best-answers.md）")
    iv.add_argument("--trace", default=None, help="trace JSONL 路径（默认 my/trace.jsonl）")
    iv.add_argument("--no-trace", action="store_true", help="本次不写 trace")
    iv.add_argument("-o", "--out", help="报告 Markdown 输出路径")
    iv.set_defaults(func=cmd_interview)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
