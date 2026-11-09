"""trace 落盘测试：不联网，用假 LLM 验证四类事件都能写出来。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app import trace as trace_mod
from app.budget import BudgetedLLM
from app.kb import KnowledgeBase
from app.scoring import score_answer


class FakeLLM:
    model = "fake"
    temperature = None

    def chat(self, messages, tools=None):
        msg = SimpleNamespace(content='{"correctness":4,"depth":3,"engineering":3,"boundary":4,"structure":4,"expression":4,"fact_mismatch":false,"covered":["1"],"missing":["2"],"evidence":["x"],"summary":"ok","strengths":[],"weaknesses":[]}')
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class TraceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "trace.jsonl"
        trace_mod.configure(str(self.path), enabled=True)
        trace_mod.set_scene("test")

    def tearDown(self):
        trace_mod._TRACER = None
        self.tmp.cleanup()

    def _lines(self):
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def test_llm_call_recorded(self):
        BudgetedLLM(FakeLLM(), max_calls=1).chat([{"role": "user", "content": "hi"}])
        rec = self._lines()[0]
        self.assertEqual(rec["kind"], "llm")
        self.assertEqual(rec["scene"], "test")
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["messages"][0]["chars"], 2)
        self.assertIn("latency_ms", rec)

    def test_kb_and_score_records(self):
        kb = KnowledgeBase(Path(self.tmp.name) / "missing.md")  # 空知识库：只验证 kb 事件，不依赖真实文件
        hit = kb.retrieve("随便问一句")
        self.assertIsNone(hit)
        item = score_answer(FakeLLM(), "q", "a", reference=None)
        kinds = [r["kind"] for r in self._lines()]
        self.assertEqual(kinds[0], "kb")
        self.assertEqual(kinds[1], "score")
        score = self._lines()[1]
        self.assertAlmostEqual(score["total"], item["total"])
        self.assertFalse(score["cap_applied"])

    def test_disabled_writes_nothing(self):
        trace_mod.configure(str(self.path), enabled=False)
        BudgetedLLM(FakeLLM(), max_calls=1).chat([{"role": "user", "content": "hi"}])
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
