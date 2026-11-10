"""值域约束 + request_id 落库的两组回归。

这两件事都属于「参数/字段写在 schema 里、但从不真正生效」的同类风险：
值域约束要验证「schema 里看得见」且「越界真的被拒」；
request_id 要验证「进库」且「报告里读得回来」，否则等于只是加了个字段名。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.session import SessionStore
from app.report import session_report_markdown
from app.tools import PARAM_LIMITS, schema_for, validate_args


def _fk_tool(score: int, tags: list[str] | None = None) -> str:
    """临时数字参数函数，用来验证 minimum/maximum 这条路径（当前业务工具还没有 int 参数）。"""
    return f"{score}{tags}"


class SchemaLimitTest(unittest.TestCase):
    def test_string_length_limits_in_schema(self):
        schema = schema_for(lambda note: note, name="save_feedback")
        prop = schema["function"]["parameters"]["properties"]["note"]
        self.assertEqual(prop.get("minLength"), 1)
        self.assertEqual(prop.get("maxLength"), 200)
        # 没配约束的参数不应凭空长出关键字
        schema2 = schema_for(lambda note: note, name="other_tool")
        self.assertNotIn("minLength", schema2["function"]["parameters"]["properties"]["note"])

    def test_numeric_limits_inject_into_schema(self):
        key = ("tmp_score_tool", "score")
        PARAM_LIMITS[key] = {"minimum": 0, "maximum": 100}
        try:
            schema = schema_for(_fk_tool, name="tmp_score_tool")
            prop = schema["function"]["parameters"]["properties"]["score"]
            self.assertEqual(prop["minimum"], 0)
            self.assertEqual(prop["maximum"], 100)
        finally:
            PARAM_LIMITS.pop(key, None)

    def test_out_of_range_is_rejected(self):
        schemas = [schema_for(lambda note: note, name="save_feedback")]
        self.assertEqual(validate_args(schemas, "save_feedback", '{"note": "还好"}')["note"], "还好")
        for bad in ('{"note": ""}', '{"note": "' + "长" * 201 + '"}', '{"note": %s}' % 123):
            with self.assertRaises(ValueError):
                validate_args(schemas, "save_feedback", bad)

    def test_numeric_out_of_range_rejected(self):
        key = ("tmp_score_tool", "score")
        PARAM_LIMITS[key] = {"minimum": 0, "maximum": 100}
        try:
            schemas = [schema_for(_fk_tool, name="tmp_score_tool")]
            self.assertEqual(validate_args(schemas, "tmp_score_tool", '{"score": 50}')["score"], 50)
            for bad in ('{"score": -1}', '{"score": 101}'):
                with self.assertRaises(ValueError):
                    validate_args(schemas, "tmp_score_tool", bad)
        finally:
            PARAM_LIMITS.pop(key, None)


class RequestIdTraceTest(unittest.TestCase):
    def test_turn_persists_request_id_and_report_shows_it(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "t.db")
            store = SessionStore(db)
            sid = store.create_session("后端", {"questions": [{"id": "Q1", "topic": "状态机"}]})
            store.add_turn(sid, 1, {"id": "Q1", "qid": "Q1", "topic": "状态机", "answer": "a",
                                    "total": 3.5, "scores": {}, "request_id": "web:Q1-abc12345"})
            read = store.turns(sid)
            store.close()
            self.assertEqual(read[0]["request_id"], "web:Q1-abc12345")
            md = session_report_markdown({"questions": []}, read)
            self.assertIn("web:Q1-abc12345", md)
            self.assertIn("grep", md)

    def test_old_rows_without_request_id_are_empty(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "old.db")
            store = SessionStore(db)
            sid = store.create_session("后端", {"questions": []})
            store.conn.execute(
                "INSERT INTO turns(session_id, idx, qid, topic, question, answer, scores_json,"
                " total, summary, evidence_json, strengths_json, weaknesses_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, 1, "Q1", "状态机", "q", "a", "{}", 3.0, "", "[]", "[]", "[]"),
            )
            store.conn.commit()
            read = store.turns(sid)
            store.close()
            self.assertEqual(read[0]["request_id"], "")


class KBParseGuardTest(unittest.TestCase):
    """文档措辞一变，解析就整库为空——这种失败必须留下痕迹，不能静默。"""

    def _kb(self, body: str):
        from app.kb import KnowledgeBase
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kb.md"
            p.write_text(body, encoding="utf-8")
            return KnowledgeBase(p)

    def test_alias_and_middle_dot(self):
        body = ("## Q1・测试题\n\n**一句话**：x\n\n**怎么说（可直接背）**\n\n- a\n- b\n- c\n\n"
                "**关键数字**：3 个\n\n**边界**：别吹\n")
        kb = self._kb(body)
        self.assertEqual(len(kb.sections), 1)
        self.assertEqual(kb.sections[0]["qid"], "Q1")
        self.assertGreaterEqual(len(kb.sections[0]["points"]), 3)  # 别名映射到了「讲法」
        self.assertEqual(kb.errors, [])

    def test_all_sections_unparsed_raises_flag(self):
        kb = self._kb("## Q1・测试\n\n**说说**：y\n\n## Q2・测试\n\n**讲讲**：z\n")
        self.assertEqual(len(kb.sections), 0)
        self.assertTrue(kb.errors)  # 有题目标题却零解析 → 必须留错误
        self.assertFalse(kb.available)


if __name__ == "__main__":
    unittest.main()
