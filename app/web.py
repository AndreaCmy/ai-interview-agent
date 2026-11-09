"""P4：FastAPI 单页界面。复用 P2 的打分 / 落库 / 报告逻辑，不另写一套规则。

三条约束（和 CLI 保持同一口径）：
1. 每题只调 1 次 LLM（score_answer），报告再 1 次（generate_report）；
2. 调用次数由 BudgetedLLM 硬限制，默认 = 题数 + 1；
3. 总分由 app/scoring.py 在代码端重算，页面只负责展示。

启动：./practice.sh web   → http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .budget import BudgetedLLM, BudgetExceeded
from .interview import generate_report
from .llm import RealLLM
from .report import bank_to_markdown, session_report_markdown
from .scoring import DIM_CN, DIMENSIONS, WEIGHTS, score_answer
from .session import SessionStore

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
BANK_DEFAULT = ROOT / "my" / "bank.json"
DB_DEFAULT = pathlib.Path(os.getenv("WEB_DB", str(ROOT / "my" / "web-interview.db")))
DEFAULT_ROLE = "AI Agent / 后端"

app = FastAPI(title="AI 面试 Agent · Web")


class State:
    """单用户内存态：一次只跑一场面试。"""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.bank: Optional[Dict[str, Any]] = None
        self.role: str = DEFAULT_ROLE
        self.questions: List[Dict[str, Any]] = []
        self.turns: List[Dict[str, Any]] = []
        self.session_id: Optional[int] = None
        self.llm: Optional[BudgetedLLM] = None
        self.report_md: Optional[str] = None
        self.report_path: Optional[str] = None


state = State()


def _load_bank(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        raise HTTPException(400, f"找不到题库：{path}（先跑 ./practice.sh prep）")
    return json.loads(path.read_text(encoding="utf-8"))


def _make_llm(max_calls: int) -> BudgetedLLM:
    try:
        return BudgetedLLM(RealLLM(), max_calls=max_calls, label="[LLM 调用")
    except RuntimeError as e:
        raise HTTPException(400, str(e))


def _card(item: Dict[str, Any]) -> Dict[str, Any]:
    """把打分结果整理成前端要的形状（含中文维度名与权重）。"""
    return {
        "id": item.get("id", ""),
        "topic": item.get("topic", ""),
        "category": item.get("category", ""),
        "question": item.get("question", ""),
        "answer": item.get("answer", ""),
        "total": round(float(item.get("total", 0)), 2),
        "dims": [
            {"key": d, "name": DIM_CN[d], "score": round(float(item["scores"].get(d, 0)), 1),
             "weight": WEIGHTS[d]}
            for d in DIMENSIONS
        ],
        "summary": item.get("summary", ""),
        "strengths": item.get("strengths", []),
        "weaknesses": item.get("weaknesses", []),
        "evidence": item.get("evidence", []),
    }


class StartReq(BaseModel):
    bank: Optional[str] = None
    role: Optional[str] = None


class AnswerReq(BaseModel):
    index: int
    answer: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/state")
def get_state() -> Dict[str, Any]:
    return {
        "role": state.role,
        "session_id": state.session_id,
        "started": state.bank is not None,
        "questions": state.questions,
        "turns": [_card(t) for t in state.turns],
        "calls": {"used": state.llm.calls if state.llm else 0,
                  "max": state.llm.max_calls if state.llm else len(state.questions) + 1},
        "report_md": state.report_md,
        "report_path": state.report_path,
    }


@app.post("/api/session")
def start_session(req: StartReq) -> Dict[str, Any]:
    bank_path = pathlib.Path(req.bank) if req.bank else BANK_DEFAULT
    bank = _load_bank(bank_path)
    questions = bank.get("questions", [])
    if not questions:
        raise HTTPException(400, "题库里没有题目")
    role = req.role or bank.get("role") or DEFAULT_ROLE

    state.reset()
    state.bank, state.role = bank, role
    state.questions = [
        {"id": q.get("id", f"Q{i}"), "topic": q.get("topic", ""),
         "category": q.get("category", "技术"), "question": q.get("question", "")}
        for i, q in enumerate(questions, 1)
    ]
    max_calls = int(os.getenv("WEB_MAX_CALLS", str(len(questions) + 1)))
    state.llm = _make_llm(max_calls)
    store = SessionStore(str(DB_DEFAULT))
    state.session_id = store.create_session(role, bank)
    store.close()
    return {"ok": True, "role": role, "session_id": state.session_id,
            "questions": state.questions, "max_calls": max_calls}


@app.post("/api/answer")
def answer(req: AnswerReq) -> Dict[str, Any]:
    if not state.bank or state.llm is None:
        raise HTTPException(400, "还没有开始面试")
    if not (1 <= req.index <= len(state.questions)):
        raise HTTPException(400, "题号超出范围")
    text = (req.answer or "").strip()
    if not text:
        raise HTTPException(400, "回答不能为空（空答不计分）")

    q = state.questions[req.index - 1]
    try:
        item = score_answer(state.llm, q["question"], text, role=state.role)
    except BudgetExceeded as e:
        raise HTTPException(429, f"预算用尽：{e}")

    item["id"] = q["id"]
    item["topic"] = q["topic"]
    item["category"] = q["category"]
    state.turns = [t for t in state.turns if t.get("id") != q["id"]]
    state.turns.append(item)
    store = SessionStore(str(DB_DEFAULT))
    store.add_turn(int(state.session_id), len(state.turns), item)
    store.close()
    return {"ok": True, "card": _card(item),
            "calls": {"used": state.llm.calls, "max": state.llm.max_calls}}


@app.post("/api/report")
def make_report() -> Dict[str, Any]:
    if not state.turns:
        raise HTTPException(400, "还没有已计分的题目")
    store = SessionStore(str(DB_DEFAULT))
    store.finish(int(state.session_id))
    store.close()

    data = generate_report(state.llm, state.bank or {}, state.turns, state.role) if state.llm else None
    md = session_report_markdown(state.bank or {}, state.turns, data)
    out = ROOT / "my" / f"report-web-{time.strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(md, encoding="utf-8")
    state.report_md, state.report_path = md, str(out)
    return {"ok": True, "report_md": md, "report_path": str(out),
            "degraded": data is None,
            "calls": {"used": state.llm.calls if state.llm else 0,
                      "max": state.llm.max_calls if state.llm else 0}}


@app.get("/api/replay/{sid}")
def replay(sid: int) -> Dict[str, Any]:
    """读历史会话（零 LLM 调用），用于回放/展示。"""
    store = SessionStore(str(DB_DEFAULT))
    turns = store.turns(sid)
    store.close()
    if not turns:
        raise HTTPException(404, f"会话 #{sid} 没有记录")
    cards = []
    for t in turns:
        item = dict(t)
        item.setdefault("id", f"Q{t.get('idx')}")
        cards.append(_card(item))
    return {"ok": True, "session_id": sid, "turns": cards,
            "report_md": state.report_md if state.session_id == sid else None}


@app.get("/api/bank/preview")
def bank_preview() -> Dict[str, Any]:
    bank = _load_bank(BANK_DEFAULT)
    return {"ok": True, "count": len(bank.get("questions", [])),
            "markdown": bank_to_markdown(bank)[:4000]}


def main(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main(port=int(os.getenv("PORT", "8000")))
