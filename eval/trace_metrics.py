"""从 trace 现算运行指标（零调用）：延迟、封顶率、召回命中率、调用分布。

为什么值得单独算：这些问题本来就在盘上，但只有聚合成表才看得出来——
比如「封顶率」高说明事实不符判定在起作用，而「召回命中率 100%」说明阈值没有否决力。
用法：`.venv/bin/python -m eval.trace_metrics [--trace my/trace.jsonl]`
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def _pct(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round((len(s) - 1) * q))))
    return round(s[k], 1)


def collect(path: Path) -> dict:
    kinds = Counter()
    llm_ms: list[float] = []
    tool_ok: Counter = Counter()
    kb_scores: list[float] = []
    kb_hit: Counter = Counter()
    capped: Counter = Counter()
    totals: list[float] = []
    per_request = defaultdict(set)
    stub = 0

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        kind = d.get("kind")
        kinds[kind] += 1
        if d.get("request_id"):
            per_request[d["request_id"]].add(kind)
        if kind == "llm":
            # latency_ms=0 是 StubLLM / 离线回放写的事件，不是「真的很快」；
            # 混进分位数会把 p50 压成 0，看着像没延迟。真调用才统计。
            if isinstance(d.get("latency_ms"), (int, float)) and d["latency_ms"] > 0:
                llm_ms.append(float(d["latency_ms"]))
            else:
                stub += 1
        elif kind == "tool":
            tool_ok["ok" if d.get("ok") else "fail"] += 1
        elif kind == "kb":
            if isinstance(d.get("score"), (int, float)):
                kb_scores.append(float(d["score"]))
            kb_hit["hit" if d.get("hit") else "miss"] += 1
        elif kind == "score":
            capped["capped" if d.get("cap_reason") else "normal"] += 1
            if isinstance(d.get("total"), (int, float)):
                totals.append(float(d["total"]))

    return {
        "kinds": kinds,
        "llm_ms": llm_ms,
        "tool": tool_ok,
        "kb_scores": kb_scores,
        "kb_hit": kb_hit,
        "capped": capped,
        "totals": totals,
        "requests": len(per_request),
        "req_kinds": Counter(len(v) for v in per_request.values()),
        "stub_llm": stub,
    }


def render(r: dict) -> str:
    lines = ["| 指标 | 值 |", "| --- | --- |"]
    lines.append(f"| 事件总数 | {sum(r['kinds'].values())} |")
    lines.append("| 事件构成 | " + "、".join(f"{k} {v}" for k, v in r["kinds"].most_common()) + " |")
    if r["llm_ms"]:
        lines.append(
            f"| LLM 真实延迟 | p50 {_pct(r['llm_ms'], 0.5)} ms ／ p95 {_pct(r['llm_ms'], 0.95)} ms"
            f"（{len(r['llm_ms'])} 次真调用，另有 {r['stub_llm']} 次 stub/回放未计） |"
        )
    if r["tool"]:
        fail = r["tool"].get("fail", 0)
        total = sum(r["tool"].values())
        lines.append(f"| 工具失败率 | {fail}/{total}（{fail / total:.0%}） |")
    if r["kb_scores"]:
        lines.append(
            f"| 召回分 | 区间 {min(r['kb_scores']):.2f}–{max(r['kb_scores']):.2f}，中位 "
            f"{statistics.median(r['kb_scores']):.2f}（{len(r['kb_scores'])} 次） |"
        )
        hit = r["kb_hit"].get("hit", 0)
        total = sum(r["kb_hit"].values())
        lines.append(f"| 召回命中率 | {hit}/{total}（{hit / total:.0%}） |")
    if r["capped"]:
        c = r["capped"].get("capped", 0)
        total = sum(r["capped"].values())
        lines.append(f"| 事实不符封顶率 | {c}/{total}（{c / total:.0%}） |")
    if r["totals"]:
        lines.append(f"| 打分均值 | {statistics.mean(r['totals']):.2f}（{len(r['totals'])} 题） |")
    if r["requests"]:
        lines.append(f"| 请求窗口 | {r['requests']} 个，平均 {sum(k * v for k, v in r['req_kinds'].items()) / r['requests']:.1f} 类事件/窗口 |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", default=str(Path("my") / "trace.jsonl"))
    a = ap.parse_args()
    p = Path(a.trace)
    if not p.exists():
        print(f"没有找到 {p}")
        return
    print(render(collect(p)))


if __name__ == "__main__":
    main()
