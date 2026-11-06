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
                session_id INTEGER, idx INTEGER, topic TEXT, question TEXT, answer TEXT,
                scores_json TEXT, total REAL, summary TEXT,
                evidence_json TEXT, strengths_json TEXT, weaknesses_json TEXT)"""
        )
        self.conn.commit()

    def create_session(self, role: str, bank: Dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO sessions(role, created_at, bank_json, status) VALUES(?,?,?,?)",
            (role, time.strftime("%Y-%m-%d %H:%M:%S"), json.dumps(bank, ensure_ascii=False), "in_progress"),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_turn(self, session_id: int, idx: int, item: Dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO turns(session_id, idx, topic, question, answer, scores_json,
                total, summary, evidence_json, strengths_json, weaknesses_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id,
                idx,
                item.get("topic", ""),
                item.get("question", ""),
                item.get("answer", ""),
                json.dumps(item.get("scores", {}), ensure_ascii=False),
                float(item.get("total", 0.0)),
                item.get("summary", ""),
                json.dumps(item.get("evidence", []), ensure_ascii=False),
                json.dumps(item.get("strengths", []), ensure_ascii=False),
                json.dumps(item.get("weaknesses", []), ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def finish(self, session_id: int) -> None:
        self.conn.execute("UPDATE sessions SET status='finished' WHERE id=?", (session_id,))
        self.conn.commit()

    def turns(self, session_id: int) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT idx, topic, question, answer, scores_json, total, summary,
                      evidence_json, strengths_json, weaknesses_json
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
            })
        return out

    def close(self) -> None:
        self.conn.close()
