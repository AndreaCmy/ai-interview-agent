"""跑 golden set：每题每档打分，检查「排序是否与构造先验一致」，可开关检索。

用法：
  python -m eval.run --tag on              # 接检索（每题 1 次调用）
  python -m eval.run --tag off --no-kb --tiers bad   # 关检索，只跑差档做对照

指标：
- rank_ok：同一题内 good > mid > bad（分数严格递增），这是打分器最核心的能力；
- margin：good 与 bad 的分差，太小说明打分器分不出好坏；
- covered：命中要点数，用来看检索是否真的被用上（关检索时应为 0）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.budget import BudgetedLLM  # noqa: E402
from app.kb import DEFAULT_KB, KnowledgeBase  # noqa: E402
from app.llm import RealLLM  # noqa: E402
from app.scoring import score_answer  # noqa: E402

GOLDEN = ROOT / "eval" / "golden.jsonl"
RANK = {"bad": 1, "mid": 2, "good": 3}


def load_cases(path: pathlib.Path, tiers: list[str] | None, limit: int | None) -> list[dict]:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if tiers:
        rows = [r for r in rows if r["tier"] in tiers]
    if limit:
        rows = rows[:limit]
    return rows


def summarize(cases: list[dict]) -> list[dict]:
    """按题聚合：排序是否成立、最大与最小分差。"""
    by_q: dict[str, dict] = {}
    for c in cases:
        by_q.setdefault(c["qid"], {})[c["tier"]] = c
    out = []
    for qid, tiers in sorted(by_q.items()):
        vals = {k: v["total"] for k, v in tiers.items()}
        ordered = [k for k in sorted(vals, key=lambda x: vals[x])]
        expect = [k for k in sorted(vals, key=lambda x: RANK[x])]
        ok = ordered == expect
        margin = round(max(vals.values()) - min(vals.values()), 2) if len(vals) > 1 else 0.0
        out.append({"qid": qid, "ok": ok, "margin": margin, "scores": vals,
                    "got_order": ordered, "topic": next(iter(tiers.values()))["topic"]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="on", help="本轮标签，决定输出文件名")
    ap.add_argument("--golden", default=str(GOLDEN))
    ap.add_argument("--no-kb", action="store_true", help="关闭检索（做对照基线）")
    ap.add_argument("--kb", default=str(DEFAULT_KB))
    ap.add_argument("--tiers", nargs="*", default=None, help="只跑指定档位，如 bad mid")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--qids", nargs="*", default=None, help="只跑指定题号，如 Q10")
    args = ap.parse_args()

    cases = load_cases(pathlib.Path(args.golden), args.tiers, args.limit)
    if args.qids:
        cases = [c for c in cases if c["qid"] in args.qids]
    if not cases:
        print("[错误] golden set 为空")
        return 1

    llm = BudgetedLLM(RealLLM(), len(cases), label="LLM")
    kb = None if args.no_kb else KnowledgeBase(args.kb)
    if kb is not None:
        print(f"[知识库] {kb.path} | 段落 {len(kb.sections)}")
    else:
        print("[知识库] 已关闭（对照基线）")

    for i, c in enumerate(cases, 1):
        ref = None
        if kb is not None:
            ref = kb.retrieve(c["question"], c.get("topic", ""))
        item = score_answer(llm, c["question"], c["answer"], role="AI Agent / 后端", reference=ref)
        c["total"] = item["total"]
        c["covered"] = len(item["covered"])
        c["missing"] = len(item["missing"])
        c["scores"] = item["scores"]
        c["summary"] = item["summary"]
        c["weaknesses"] = item["weaknesses"]
        print(f"[{i}/{len(cases)}] {c['id']:<10} {item['total']:.2f}  命中{item['covered']}/漏{item['missing']}")

    summary = summarize(cases)
    out = ROOT / "eval" / f"last-{args.tag}.json"
    out.write_text(json.dumps({"tag": args.tag, "kb": not args.no_kb, "cases": cases,
                               "summary": summary}, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n## 排序一致性")
    print("| 题 | 期望 good>mid>bad | 实际顺序 | 分差 | 分数 |")
    print("| --- | --- | --- | --- | --- |")
    for s in summary:
        sc = " / ".join(f"{k}:{v:.2f}" for k, v in s["scores"].items())
        print(f"| {s['qid']} | {'✅' if s['ok'] else '❌'} | {'<'.join(s['got_order'])} | {s['margin']} | {sc} |")

    ok = sum(1 for s in summary if s["ok"])
    avg_margin = sum(s["margin"] for s in summary) / len(summary) if summary else 0
    print(f"\n**排序正确**：{ok}/{len(summary)} | **平均分差**：{avg_margin:.2f} | 调用 {llm.calls}/{llm.max_calls}")
    print(f"[已写入] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
