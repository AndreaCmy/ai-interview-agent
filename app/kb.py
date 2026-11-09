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
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_KB = ROOT / "my" / "best-answers.md"

# 段落字段名（与 my/best-answers.md 的写法对应）
FIELDS = ("一句话", "讲法", "关键数字", "会被追问", "边界")


def _tokens(text: str) -> set:
    """英文词 + 中文双字。中文按字符二元组切，够用且不引入分词依赖。"""
    low = text.lower()
    toks = set(re.findall(r"[a-z0-9_]+", low))
    cjk = re.findall(r"[\u4e00-\u9fff]", low)
    toks.update(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return toks


def _field_key(raw: str) -> str:
    """把「边界（最重要）」这类带括注的小标题归一成字段名。"""
    return re.sub(r"[（(].*?[）)]", "", raw).strip()


def _parse_section(chunk: str) -> Optional[Dict[str, Any]]:
    lines = chunk.splitlines()
    if not lines:
        return None
    head = re.match(r"##\s+(Q\d+)\s*·\s*(.+)", lines[0].strip())
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
        for ch in chunks:
            sec = _parse_section(ch)
            if sec and sec["points"]:
                self.sections.append(sec)

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

    def retrieve(self, question: str, topic: str = "", min_score: float = 3.0) -> Optional[Dict[str, Any]]:
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
