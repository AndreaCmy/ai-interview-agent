"""从 trace 里现算召回分分布，给阈值取值找依据（零调用）。

为什么单独有这个脚本：阈值是个「看不见的参数」——它写在那儿，但如果取值低于整个
分数分布，就一次也不会否决任何召回，等于没有。判断它有没有生效，只能拿实测分布比。

用法：
    .venv/bin/python -m eval.threshold_scan [--trace my/trace.jsonl] [--steps 35 40 45 50 55]

输出：分布摘要 + 各候选阈值下的命中率，以及「当前阈值把分布切在哪个位置」。
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from app.kb import RECALL_MIN_SCORE


def scan(path: Path, candidates: list[float]) -> dict:
    scores, mins = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("kind") != "kb":
            continue
        if isinstance(d.get("score"), (int, float)):
            scores.append(float(d["score"]))
        if isinstance(d.get("min_score"), (int, float)):
            mins.append(float(d["min_score"]))
    if not scores:
        return {"n": 0}

    rows = []
    for th in candidates:
        hit = sum(1 for v in scores if v >= th)
        rows.append({"threshold": th, "hit": hit, "rate": round(hit / len(scores), 3)})
    return {
        "n": len(scores),
        "min": round(min(scores), 2),
        "max": round(max(scores), 2),
        "p50": round(statistics.median(scores), 2),
        "p10": round(sorted(scores)[len(scores) // 10], 2),
        "current": RECALL_MIN_SCORE,
        "current_hit": sum(1 for v in scores if v >= RECALL_MIN_SCORE),
        "observed_mins": sorted(set(mins)),
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", default=str(Path("my") / "trace.jsonl"))
    ap.add_argument("--steps", nargs="*", type=float, default=[3.0, 35.0, 40.0, 45.0, 50.0, 55.0, 58.0])
    a = ap.parse_args()
    r = scan(Path(a.trace), a.steps)
    if not r.get("n"):
        print(f"{a.trace} 里没有带 score 的 kb 事件")
        return
    print(f"## 召回分实测分布（{r['n']} 次）")
    print(f"- 区间 {r['min']} – {r['max']}，中位 {r['p50']}，p10 {r['p10']}")
    print(f"- 历史 min\\_score 取值：{r['observed_mins']}")
    print()
    print("| 阈值 | 命中 | 命中率 |")
    print("| --- | --- | --- |")
    for row in r["rows"]:
        mark = "（当前）" if row["threshold"] == r["current"] else ""
        print(f"| {row['threshold']}{mark} | {row['hit']}/{r['n']} | {row['rate']:.0%} |")


if __name__ == "__main__":
    main()
