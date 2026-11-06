"""P1/P2 离线单测（不调用真实 LLM，用 FakeLLM 返回固定 JSON）。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.jsonutil import extract_json
from app.scoring import score_answer
from app.prep import generate_bank
from app.report import bank_to_markdown, score_to_markdown, session_report_markdown
from app.budget import BudgetedLLM, BudgetExceeded
from app.session import SessionStore


class FakeLLM:
    def __init__(self, payload: str):
        self.payload = payload
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        msg = SimpleNamespace(content=self.payload, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class TestP1(unittest.TestCase):
    def test_extract_json_fenced(self):
        text = '```json\n{"a": 1}\n```'
        self.assertEqual(extract_json(text), {"a": 1})

    def test_extract_json_with_prefix(self):
        text = '好的，结果是：{"a": 2} 以上。'
        self.assertEqual(extract_json(text), {"a": 2})

    def test_score_answer_six_dims_recompute_total(self):
        payload = (
            '{"correctness":5,"depth":4,"engineering":3,"boundary":4,'
            '"structure":3,"expression":4,"total":99,"evidence":["我用了 Redis 缓存"],'
            '"summary":"总体不错","strengths":["有数据"],"weaknesses":["深度不足"]}'
        )
        item = score_answer(FakeLLM(payload), "讲讲缓存设计", "我用了 Redis 缓存...")
        self.assertEqual(item["scores"]["correctness"], 5.0)
        self.assertEqual(item["scores"]["engineering"], 3.0)
        # 六维加权：5*.3+4*.2+3*.2+4*.1+3*.1+4*.1 = 4.0，忽略模型给的 99
        self.assertAlmostEqual(item["total"], 4.0, places=2)
        md = score_to_markdown(item)
        self.assertIn("工程落地", md)
        self.assertIn("Redis", md)

    def test_score_answer_clamps_out_of_range(self):
        payload = '{"correctness":9,"depth":-2,"engineering":3,"boundary":4,"structure":3,"expression":4}'
        item = score_answer(FakeLLM(payload), "q", "a")
        self.assertEqual(item["scores"]["correctness"], 5.0)
        self.assertEqual(item["scores"]["depth"], 0.0)

    def test_generate_bank(self):
        payload = (
            '{"questions":[{"id":"Q1","category":"技术","topic":"ReAct",'
            '"question":"讲讲 ReAct 循环","points":["循环步骤"],"followups":["如何防死循环"]}]}'
        )
        bank = generate_bank(FakeLLM(payload), "jd", "resume")
        self.assertEqual(bank["count"], 1)
        md = bank_to_markdown(bank)
        self.assertIn("讲讲 ReAct 循环", md)
        self.assertIn("如何防死循环", md)


class TestP2(unittest.TestCase):
    def test_budget_guard(self):
        inner = FakeLLM("{}")
        b = BudgetedLLM(inner, 2)
        b.chat([])
        b.chat([])
        with self.assertRaises(BudgetExceeded):
            b.chat([])
        self.assertEqual(b.calls, 2)

    def test_session_store_roundtrip(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = SessionStore(path)
            sid = store.create_session("AI Agent", {"questions": []})
            store.add_turn(sid, 1, {
                "topic": "ReAct", "question": "q", "answer": "a",
                "scores": {"correctness": 5}, "total": 4.0, "summary": "s",
                "evidence": ["e"], "strengths": [], "weaknesses": ["w"],
            })
            turns = store.turns(sid)
            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]["total"], 4.0)
            self.assertEqual(turns[0]["weaknesses"], ["w"])
            store.close()
        finally:
            os.remove(path)

    def test_session_report_fallback_and_llm(self):
        bank = {"questions": [{"id": "Q1", "topic": "ReAct", "category": "技术", "question": "q1"}]}
        turns = [{"id": "Q1", "topic": "ReAct", "question": "q1", "answer": "a1", "total": 2.0, "weaknesses": ["缺深度"]}]
        md = session_report_markdown(bank, turns, None)
        self.assertIn("复习优先级", md)
        self.assertIn("缺深度", md)

        rpt = {
            "overall": "整场还行",
            "per_question": [{"id": "Q1", "improved_answer": "改进版答案", "key_points": ["k1"]}],
            "priority": [{"id": "Q1", "reason": "最弱"}],
        }
        md2 = session_report_markdown(bank, turns, rpt)
        self.assertIn("改进版答案", md2)
        self.assertIn("最弱", md2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
