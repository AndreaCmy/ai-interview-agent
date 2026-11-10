"""这一轮五项加固的测试：qid / stop_reason / 重复调用 / cap_reason / request_id / 参数校验。

都不调真实 LLM，用 FakeLLM 或直接构造 schema。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import run_agent
from app.scoring import FACT_MISMATCH_CAP, score_answer
from app.session import SessionStore
from app.tools import schema_for, validate_args
from app.trace import Tracer, configure


def _resp(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _tool_call(name: str, args: dict, cid: str = "call_1"):
    return SimpleNamespace(
        id=cid,
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )


class ScriptedLLM:
    """按脚本逐次返回；脚本用完后一直返回最后一条。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        return step


class StopReasonTest(unittest.TestCase):
    def test_answered(self):
        res = run_agent(ScriptedLLM([_resp("直接回答")]), "sys", "u", [], {}, verbose=False)
        self.assertEqual(res["stop_reason"], "answered")
        self.assertEqual(res["final"], "直接回答")

    def test_max_iter(self):
        # 每轮都给一个不同的工具调用，永远不收敛
        script = [_resp("", [_tool_call("t", {"i": i}, f"c{i}")]) for i in range(5)]
        res = run_agent(ScriptedLLM(script), "sys", "u", [], {"t": lambda i: i}, max_iter=3, verbose=False)
        self.assertEqual(res["stop_reason"], "max_iter")
        self.assertIsNone(res["final"])

    def test_repeated_tool_call_cuts_loop(self):
        # 同一个工具、同一组参数反复调：第 2、3 次被判为原地打转
        tc = _tool_call("next_question", {})
        script = [_resp("", [tc]) for _ in range(8)]
        res = run_agent(
            ScriptedLLM(script), "sys", "u",
            [schema_for(lambda: "q", "next_question")],
            {"next_question": lambda: "q"},
            max_iter=8, verbose=False,
        )
        self.assertEqual(res["stop_reason"], "repeated_tool_call")
        # 重复的调用没有真的执行，只跑了 1 次
        self.assertLess(res["iterations"], 8)


class ArgsValidationTest(unittest.TestCase):
    def setUp(self):
        def sample(count: int, mode: str = "fast", tags: list[str] | None = None) -> str:
            """示例工具。

            Args:
                count: 次数
            """
            return ""

        self.schema = schema_for(sample, "sample")
        self.schemas = [self.schema]

    def test_type_ok_and_coerced(self):
        out = validate_args(self.schemas, "sample", '{"count": 3}')
        self.assertEqual(out["count"], 3)
        # 没给的参数不塞 None 进去，交给函数自己的默认值（塞 None 会覆盖掉默认值）
        self.assertNotIn("mode", out)

    def test_wrong_type_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_args(self.schemas, "sample", '{"count": "三次"}')
        self.assertIn("count", str(ctx.exception))

    def test_required_missing_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_args(self.schemas, "sample", "{}")
        self.assertIn("count", str(ctx.exception))

    def test_bad_json_rejected(self):
        with self.assertRaises(ValueError):
            validate_args(self.schemas, "sample", "{count:3")

    def test_unknown_tool_rejected(self):
        with self.assertRaises(ValueError):
            validate_args(self.schemas, "ghost", "{}")

    def test_invalid_args_fed_back_to_model(self):
        """校验失败不炸循环，而是变成 role=tool 的 error 回喂，模型下一轮自己改。"""
        script = [
            _resp("", [_tool_call("next_question", {"bogus": 1})]),
            _resp("修好了，最终回答"),
        ]
        res = run_agent(
            ScriptedLLM(script), "sys", "u",
            [schema_for(lambda: "q", "next_question")],
            {"next_question": lambda: "q"},
            verbose=False,
        )
        self.assertEqual(res["stop_reason"], "answered")
        tool_msgs = [m for m in res["messages"] if m.get("role") == "tool"]
        self.assertTrue(tool_msgs)
        self.assertIn("error", str(tool_msgs[-1]["content"]))


class CapReasonTest(unittest.TestCase):
    def test_cap_reason_written(self):
        payload = json.dumps({
            "correctness": 5, "depth": 5, "engineering": 5,
            "boundary": 5, "structure": 5, "expression": 5,
            "fact_mismatch": True,
        })
        fake = ScriptedLLM([_resp(payload)])
        item = score_answer(fake, "q", "编造的回答")
        self.assertEqual(item["total"], FACT_MISMATCH_CAP)
        self.assertTrue(item["cap_applied"])
        self.assertIn("fact_mismatch", item["cap_reason"])

    def test_no_cap_no_reason(self):
        payload = json.dumps({
            "correctness": 3, "depth": 2, "engineering": 2,
            "boundary": 2, "structure": 2, "expression": 2,
            "fact_mismatch": False,
        })
        item = score_answer(ScriptedLLM([_resp(payload)]), "q", "普通回答")
        self.assertFalse(item["cap_applied"])
        self.assertEqual(item["cap_reason"], "")


class QidMigrationTest(unittest.TestCase):
    def test_old_schema_migrated_and_qid_roundtrip(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            # 先手工造一张「老版本」表：turns 没有 qid 列
            conn = sqlite3.connect(path)
            conn.execute(
                """CREATE TABLE turns(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER, idx INTEGER, topic TEXT, question TEXT, answer TEXT,
                    scores_json TEXT, total REAL, summary TEXT,
                    evidence_json TEXT, strengths_json TEXT, weaknesses_json TEXT)"""
            )
            conn.commit()
            conn.close()

            store = SessionStore(path)  # 打开时自动补列
            cols = [r[1] for r in store.conn.execute("PRAGMA table_info(turns)").fetchall()]
            self.assertIn("qid", cols)

            sid = store.create_session("AI Agent", {"questions": []})
            store.add_turn(sid, 1, {"qid": "Q1", "topic": "t", "question": "q", "answer": "a", "total": 2.0})
            store.add_turn(sid, 2, {"topic": "t2", "question": "q2", "answer": "a2", "total": 3.0})
            turns = store.turns(sid)
            self.assertEqual(turns[0]["qid"], "Q1")
            self.assertEqual(turns[1]["qid"], "")  # 老式调用没有 qid，读成空串
            store.close()
        finally:
            os.remove(path)


class RequestIdTraceTest(unittest.TestCase):
    def test_events_share_request_id(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        try:
            tracer = configure(path=path, enabled=True)
            with tracer.request(label="ask") as rid:
                tracer.kb_hit(question="q", hit=None, min_score=3.0)
                tracer.score(result={"total": 2.0}, reference=None, cap_applied=False, cap_reason="")
            lines = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertEqual(len(lines), 2)
            self.assertTrue(all(r["request_id"] == rid for r in lines))
            self.assertTrue(rid.startswith("ask-"))
            # 退出窗口后恢复为空，不会污染后续事件
            self.assertEqual(tracer.request_id, "")
            tracer.emit("kb", note="after")
            last = json.loads(Path(path).read_text(encoding="utf-8").strip().splitlines()[-1])
            self.assertEqual(last["request_id"], "")
            configure(path=None, enabled=False)  # 复位全局单例，别影响别的测试
        finally:
            os.remove(path)

    def test_cap_reason_in_score_event(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        try:
            configure(path=path, enabled=True)
            payload = json.dumps({
                "correctness": 5, "depth": 5, "engineering": 5,
                "boundary": 5, "structure": 5, "expression": 5,
                "fact_mismatch": True,
            })
            score_answer(ScriptedLLM([_resp(payload)]), "q", "a")
            rec = json.loads(Path(path).read_text(encoding="utf-8").strip().splitlines()[-1])
            self.assertEqual(rec["kind"], "score")
            self.assertTrue(rec["cap_applied"])
            self.assertIn("fact_mismatch", rec["cap_reason"])
            configure(path=None, enabled=False)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
