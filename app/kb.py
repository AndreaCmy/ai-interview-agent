"""P3 检索：把「最佳答案」文档切成按题的知识块，用关键词召回。

三条设计约束：

1. **零额外 LLM 调用**。召回纯本地计算（中文双字 + 英文词，IDF 加权），
   召回结果只是拼进本来就要发的打分 prompt，调用次数不变。
2. **只做对照，不做答案**。召回给模型的是「这道题该讲到的要点」，
   让它判断候选人命中了几条、漏了哪条，而不是拿要点冒充候选人的回答。
3. **召回为空就退化为原行为**。没有命中（分数低于阈值）时不注入任何内容，
   打分链路与 P2 完全一致，不会因为知识库缺失而报错。
"""
from __future__ import annotations

import math
import os
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional

from .trace import get_tracer

ROOT = pathlib.Path(__file__).resolve().parent.parent
# 数据目录：默认 <repo>/my；设 AI_DATA_DIR 可指向仓库外的个人目录（公开版/多机共用）
DATA_DIR = pathlib.Path(os.getenv("AI_DATA_DIR") or (ROOT / "my"))
DEFAULT_KB = DATA_DIR / "best-answers.md"

# 召回阈值：低于这个分数的命中视为「没召回」，不注入对照要点。
# 为什么是 35.0 而不是早期写的 3.0：分数刻度是 IDF 加权和（实测 212 次召回的区间
# 38.69–63.48，中位 48.46），3.0 远低于整个分布，等于永不弃权——参数在，但从不生效。
# 取 35.0 = 实测最低分再留 3.7 的余量：当前题库行为不变，但阈值回到有效区间，
# 题库扩大或题干跑偏时它会真的否决。改题库/切分方式后要重跑 eval/threshold_scan.py 复核。
RECALL_MIN_SCORE = 35.0

# 段落字段名（与 my/best-answers.md 的写法对应）
FIELDS = ("一句话", "讲法", "关键数字", "会被追问", "边界")

# 小标题别名 → 规范字段名。
# `my/best-answers.md` 合并后把「讲法要点」改成了「怎么说（可直接背）」，
# 但解析器只认规范名，改文档措辞就会静默解析成空字段（tests/test_kb 会因此挂）。
# 所以别名集中放这里：以后给某一节换更好懂的小标题，只需加一行，不用动解析逻辑。
FIELD_ALIASES = {"怎么说": "讲法", "要点": "讲法", "讲法要点": "讲法"}


def _tokens(text: str) -> set:
    """英文词 + 中文双字。中文按字符二元组切，够用且不引入分词依赖。"""
    low = text.lower()
    toks = set(re.findall(r"[a-z0-9_]+", low))
    cjk = re.findall(r"[\u4e00-\u9fff]", low)
    toks.update(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return toks


def _field_key(raw: str) -> str:
    """把「边界（最重要）」这类带括注的小标题归一成字段名（别名表见 FIELD_ALIASES）。"""
    key = re.sub(r"[（(].*?[）)]", "", raw).strip()
    return FIELD_ALIASES.get(key, key)


def _parse_section(chunk: str) -> Optional[Dict[str, Any]]:
    lines = chunk.splitlines()
    if not lines:
        return None
    # 题目标题里有两种间隔号：`·`（U+00B7）与 `・`（U+30FB，日文片假名中点）。
    # 合并文档时手写标题用了后者，解析器只认前者 → 整库静默解析成空，召回全挂。
    # 两种都接受：标题是给人读的，不该因为一个不可见字符让整条链路失效。
    head = re.match(r"##\s+(Q\d+)\s*[\u00b7\u30fb]\s*(.+)", lines[0].strip())
    if not head:
        return None
    qid, topic = head.group(1), head.group(2).strip()

    fields: Dict[str, Any] = {f: ("" if f in ("一句话", "关键数字", "边界") else []) for f in FIELDS}
    cur: Optional[str] = None

    for ln in lines[1:]:
        s = ln.strip()
        if not s or s.startswith("---"):
            continue
        # 段落头（冒号在同一行）；允许「边界（最重要）」这类带括注的写法
        m = re.match(r"\*\*(.+?)\*\*\s*[:：]\s*(.*)$", s)
        if m and _field_key(m.group(1)) in fields:
            cur = _field_key(m.group(1))
            v = m.group(2).strip()
            if isinstance(fields[cur], list):
                if v:
                    fields[cur].append(v)
            else:
                fields[cur] = v
            continue
        # 只有标题、没有内容的段落头
        m2 = re.match(r"\*\*(.+?)\*\*\s*$", s)
        if m2 and _field_key(m2.group(1)) in fields:
            cur = _field_key(m2.group(1))
            continue
        if cur is None:
            continue
        if isinstance(fields[cur], list):
            item = re.sub(r"^\d+[.、]\s*", "", s)
            item = re.sub(r"^[-*]\s+", "", item).strip()  # 只去「符号+空格」的项目符号，别吃掉 **加粗** 的星号
            if item and item not in fields[cur]:
                fields[cur].append(item)

    points = [_point_label(p) for p in fields["讲法"]]
    return {
        "qid": qid,
        "topic": topic,
        "one_liner": fields["一句话"],
        "points": points,
        "numbers": fields["关键数字"],
        "boundary": fields["边界"],
        "followups": fields["会被追问"],
    }


def _point_label(item: str) -> Dict[str, str]:
    """把「**模型与分流**：订单先按…」拆成 标签 + 全文，摘要用全文。"""
    m = re.match(r"\*\*(.+?)\*\*\s*[:：]?\s*(.*)$", item)
    if m:
        return {"label": m.group(1).strip(), "text": f"{m.group(1).strip()}：{m.group(2).strip()}"}
    text = item.strip()
    label = re.split(r"[。；，：]", text)[0][:20] or text[:20]
    return {"label": label, "text": text}


class KnowledgeBase:
    """按题召回的对照要点库。构造时解析一遍，之后每问只看本地算分。"""

    def __init__(self, path: str | pathlib.Path | None = None) -> None:
        self.path = pathlib.Path(path) if path else pathlib.Path(os.getenv("KB_PATH") or DEFAULT_KB)
        self.sections: List[Dict[str, Any]] = []
        self.errors: List[str] = []  # 解析失败的原因：不抛异常打断面试，但绝不允许静默为空
        self._idf: Dict[str, float] = {}
        self._tok: List[set] = []
        self._title_tok: List[set] = []
        if self.path.exists():
            self._load(self.path.read_text(encoding="utf-8"))

    @property
    def available(self) -> bool:
        return bool(self.sections)

    def _load(self, text: str) -> None:
        chunks = re.split(r"\n(?=##\s+Q\d)", text)
        heads = sum(1 for c in chunks if _parse_section(c) or re.match(r"##\s+Q\d", c.strip()))
        for ch in chunks:
            sec = _parse_section(ch)
            if sec and sec["points"]:
                self.sections.append(sec)

        # 自检：有题目标题却一段都没解析出来，说明字段名或标题格式对不上。
        # 这种失败此前是完全静默的——文档改个措辞，召回就整库为空、打分退回无对照，
        # 而且报告里看不出来。这里把它变成一条明确错误（stderr + trace），但仍然不抛异常：
        # 知识库坏了不该让面试打不开。
        if heads and not self.sections:
            msg = (f"{self.path.name} 里有 {heads} 个题目标题，但一段都没解析出来："
                   "检查小标题是否在 FIELDS/FIELD_ALIASES 里、标题分隔符是否为 · 或 ・")
            self.errors.append(msg)
            print(f"[KB] {msg}", file=sys.stderr)
            try:
                get_tracer().emit("kb_error", message=msg, path=str(self.path))
            except Exception:
                pass

        n = len(self.sections)
        df: Dict[str, int] = {}
        for sec in self.sections:
            body = " ".join([sec["topic"], sec["one_liner"], sec["numbers"], sec["boundary"]]
                            + [p["text"] for p in sec["points"]])
            toks = _tokens(body)
            self._tok.append(toks)
            self._title_tok.append(_tokens(sec["topic"]))
            for t in toks:
                df[t] = df.get(t, 0) + 1
        self._idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}

    def retrieve(self, question: str, topic: str = "", min_score: float = None) -> Optional[Dict[str, Any]]:
        min_score = RECALL_MIN_SCORE if min_score is None else min_score
        hit = self._retrieve(question, topic, min_score)
        try:  # 观测不能影响主流程，异常静默吞掉
            get_tracer().kb_hit(question=question, hit=hit, min_score=min_score)
        except Exception:
            pass
        return hit

    def _retrieve(self, question: str, topic: str = "", min_score: float = None) -> Optional[Dict[str, Any]]:
        min_score = RECALL_MIN_SCORE if min_score is None else min_score
        if not self.sections:
            return None
        q_tok = _tokens(f"{topic} {question}")
        if not q_tok:
            return None
        best, best_i = 0.0, -1
        for i, sec in enumerate(self.sections):
            hit = q_tok & self._tok[i]
            score = sum(self._idf.get(t, 1.0) for t in hit)
            score += 2.5 * len(q_tok & self._title_tok[i])  # 主题词命中额外加权
            if score > best:
                best, best_i = score, i
        if best_i < 0 or best < min_score:
            return None
        sec = dict(self.sections[best_i])
        sec["score"] = round(best, 2)
        return sec

    def brief(self, sec: Optional[Dict[str, Any]], max_points: int = 6, width: int = 100) -> str:
        """把召回结果压成给 prompt 用的短文本（控制输入长度）。"""
        if not sec:
            return ""
        out = [f"题目标签：{sec['topic']}（{sec['qid']}）"]
        for i, p in enumerate(sec["points"][:max_points], 1):
            text = p["text"]
            out.append(f"{i}. {text[:width]}")
        if sec.get("numbers"):
            out.append(f"关键数字：{sec['numbers'][:width]}")
        if sec.get("boundary"):
            out.append(f"容易说过的边界：{sec['boundary'][:width]}")
        return "\n".join(out)
