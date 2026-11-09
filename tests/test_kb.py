"""P3 检索层离线单测（不调用真实 LLM）。

覆盖四件事：
1. 10 道题都能召回自己对应的段落（题库与最佳答案同构时不该错位）；
2. 知识库为空 / 完全跑题时召回 None，打分退化回 P2 行为；
3. 召回只拼接 prompt，调用次数仍是 1 次/题；
4. 模型返回的编号被解析成可读标签，总分仍由代码重算。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.kb import KnowledgeBase
from app.scoring import WEIGHTS, score_answer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeLLM:
    """记录收到的 prompt，返回固定 JSON（不发真实请求）。"""

    def __init__(self) -> None:
        self.calls = 0
        self.last_user = ""

    def chat(self, messages, tools=None):
        self.calls += 1
        self.last_user = messages[-1]["content"]
        content = json.dumps({
            "correctness": 4, "depth": 4, "engineering": 4,
            "boundary": 3, "structure": 4, "expression": 4,
            "evidence": ["Redis 悲观锁"],
            "summary": "对照命中",
            "strengths": ["锁语义正确"],
            "weaknesses": ["少了存储分层"],
            "covered": ["1", "2"],
            "missing": ["3"],
        }, ensure_ascii=False)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[]))])


class KBTest(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = KnowledgeBase()

    def test_parse_ten_sections(self):
        self.assertTrue(self.kb.available)
        self.assertEqual([s["qid"] for s in self.kb.sections], [f"Q{i}" for i in range(1, 11)])
        for sec in self.kb.sections:
            self.assertGreaterEqual(len(sec["points"]), 3, sec["qid"])
            self.assertTrue(sec["numbers"], sec["qid"])
            self.assertTrue(sec["boundary"], sec["qid"])

    def test_each_bank_question_hits_its_own_section(self):
        bank = json.loads(open(os.path.join(ROOT, "my", "bank.json"), encoding="utf-8").read())
        got = []
        for q in bank["questions"]:
            sec = self.kb.retrieve(q["question"], q.get("topic", ""))
            got.append(sec["qid"] if sec else None)
        self.assertEqual(got, [q["id"] for q in bank["questions"]])

    def test_empty_kb_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "empty.md")
            open(p, "w", encoding="utf-8").write("# 空\n")
            kb = KnowledgeBase(p)
            self.assertFalse(kb.available)
            self.assertIsNone(kb.retrieve("讲讲状态机怎么设计"))

    def test_offtopic_returns_none(self):
        self.assertIsNone(self.kb.retrieve("你最喜欢的电影是什么", ""))

    def test_missing_file_is_not_fatal(self):
        kb = KnowledgeBase("/tmp/根本不存在的文件.md")
        self.assertFalse(kb.available)
        self.assertIsNone(kb.retrieve("状态机"))

    def test_prompt_injection_and_single_call(self):
        ref = self.kb.retrieve("状态机并发怎么控制冲突", "分布式状态机与履约编排")
        self.assertTrue(ref)
        self.assertEqual(ref["qid"], "Q1")

        llm = FakeLLM()
        item = score_answer(llm, "状态机并发怎么控制冲突", "用 Redis 悲观锁串行化", reference=ref)

        self.assertEqual(llm.calls, 1, "召回不得增加调用次数")
        self.assertIn("【参考要点", llm.last_user)
        self.assertIn("悲观锁", llm.last_user)
        self.assertTrue(item["covered"])
        self.assertTrue(all(not s.isdigit() for s in item["covered"]), item["covered"])
        self.assertEqual(item["reference"]["qid"], "Q1")
        expect = round(sum(item["scores"][d] * WEIGHTS[d] for d in WEIGHTS), 2)
        self.assertEqual(item["total"], expect)

    def test_without_reference_prompt_unchanged(self):
        llm = FakeLLM()
        item = score_answer(llm, "随便一题", "随便一答")
        self.assertNotIn("【参考要点", llm.last_user)
        self.assertIsNone(item["reference"])


if __name__ == "__main__":
    unittest.main()


class FactMismatchTest(unittest.TestCase):
    """事实不符要在代码端封顶：模型把叙述写得再完整，也不能当高分。"""

    def test_cap_applied_and_reported(self):
        import json as _json
        from types import SimpleNamespace
        from app.kb import KnowledgeBase
        from app.scoring import FACT_MISMATCH_CAP, score_answer

        kb = KnowledgeBase()
        ref = kb.retrieve("分布式状态机", "分布式状态机与履约编排")

        class Fake:
            def chat(self, messages, tools=None):
                payload = {"correctness": 4, "depth": 4.5, "engineering": 4.5, "boundary": 4,
                           "structure": 4.5, "expression": 4.5, "fact_mismatch": True,
                           "evidence": ["回补三万多单"], "summary": "叙述完整",
                           "strengths": [], "weaknesses": ["与本人项目事实不符"], "covered": [], "missing": []}
                m = SimpleNamespace(content=_json.dumps(payload, ensure_ascii=False), tool_calls=[])
                return SimpleNamespace(choices=[SimpleNamespace(message=m)])

        out = score_answer(Fake(), "讲一次线上故障", "回补三万多单", reference=ref)
        self.assertTrue(out["fact_mismatch"])
        self.assertLessEqual(out["total"], FACT_MISMATCH_CAP)

    def test_no_flag_keeps_score(self):
        import json as _json
        from types import SimpleNamespace
        from app.kb import KnowledgeBase
        from app.scoring import FACT_MISMATCH_CAP, score_answer

        kb = KnowledgeBase()
        ref = kb.retrieve("分布式状态机", "分布式状态机与履约编排")

        class Fake:
            def chat(self, messages, tools=None):
                payload = {"correctness": 5, "depth": 5, "engineering": 5, "boundary": 5,
                           "structure": 5, "expression": 5, "fact_mismatch": False,
                           "evidence": ["x"], "summary": "好", "strengths": [], "weaknesses": [],
                           "covered": [], "missing": []}
                m = SimpleNamespace(content=_json.dumps(payload, ensure_ascii=False), tool_calls=[])
                return SimpleNamespace(choices=[SimpleNamespace(message=m)])

        out = score_answer(Fake(), "分布式状态机", "答得很好", reference=ref)
        self.assertEqual(out["total"], 5.0)
        self.assertGreater(out["total"], FACT_MISMATCH_CAP)
