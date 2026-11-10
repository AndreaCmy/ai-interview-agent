"""面试会话持久化（SQLite）。"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

DB_DEFAULT = "interview.db"


class SessionStore:
    def __init__(self, path: str = DB_DEFAULT):
        self.path = path
        self.conn = sqlite3.connect(path)
        self._init()

    def _init(self) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT, created_at TEXT, bank_json TEXT, status TEXT)"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS turns(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER, idx INTEGER, qid TEXT, topic TEXT, question TEXT, answer TEXT,
                scores_json TEXT, total REAL, summary TEXT,
                evidence_json TEXT, strengths_json TEXT, weaknesses_json TEXT)"""
        )
        self._migrate_add_qid()
        self._migrate_add_request_id()
        self.conn.commit()

    def _migrate_add_qid(self) -> None:
        """老库（turns 没有 qid 列）自动补一列，避免已录的会话打不开。

        只 ADD COLUMN、不重建表：SQLite 的 ADD COLUMN 是 O(1) 元数据操作，
        历史行的 qid 为 NULL，读的时候按空串处理，不影响回放。
        """
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(turns)").fetchall()]
        if "qid" not in cols:
            self.conn.execute("ALTER TABLE turns ADD COLUMN qid TEXT")

    def _migrate_add_request_id(self) -> None:
        """老库补 `request_id` 列：这道题对应 trace 里那条父链的 id。

        为什么要落库：报告是「事后产物」，用户拿到报告想追「这题当时召回到了什么、
        模型原话是什么」，只能回到 trace 里搜；没有 id 就只能靠 qid + 时间猜。
        存了 id，报告里每个题都能直接 grep 到召回/模型/工具/打分四条事件。
        历史行同样为 NULL，读时按空串处理。
        """
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(turns)").fetchall()]
        if "request_id" not in cols:
            self.conn.execute("ALTER TABLE turns ADD COLUMN request_id TEXT")

    def create_session(self, role: str, bank: Dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO sessions(role, created_at, bank_json, status) VALUES(?,?,?,?)",
            (role, time.strftime("%Y-%m-%d %H:%M:%S"), json.dumps(bank, ensure_ascii=False), "in_progress"),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_turn(self, session_id: int, idx: int, item: Dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO turns(session_id, idx, qid, topic, question, answer, scores_json,
                total, summary, evidence_json, strengths_json, weaknesses_json, request_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id,
                idx,
                item.get("qid") or item.get("id", ""),  # 兼容只传 id 的旧调用
                item.get("topic", ""),
                item.get("question", ""),
                item.get("answer", ""),
                json.dumps(item.get("scores", {}), ensure_ascii=False),
                float(item.get("total", 0.0)),
                item.get("summary", ""),
                json.dumps(item.get("evidence", []), ensure_ascii=False),
                json.dumps(item.get("strengths", []), ensure_ascii=False),
                json.dumps(item.get("weaknesses", []), ensure_ascii=False),
                item.get("request_id", ""),  # 指向 trace 里这题的父链，报告里直接标出来
            ),
        )
        self.conn.commit()

    def finish(self, session_id: int) -> None:
        self.conn.execute("UPDATE sessions SET status='finished' WHERE id=?", (session_id,))
        self.conn.commit()

    def turns(self, session_id: int) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT idx, topic, question, answer, scores_json, total, summary,
                      evidence_json, strengths_json, weaknesses_json, qid, request_id
               FROM turns WHERE session_id=? ORDER BY idx""",
            (session_id,),
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "idx": r[0],
                "topic": r[1],
                "question": r[2],
                "answer": r[3],
                "scores": json.loads(r[4] or "{}"),
                "total": r[5],
                "summary": r[6],
                "evidence": json.loads(r[7] or "[]"),
                "strengths": json.loads(r[8] or "[]"),
                "weaknesses": json.loads(r[9] or "[]"),
                "qid": r[10] or "",
                "request_id": r[11] or "",
            })
        return out

    def close(self) -> None:
        self.conn.close()
