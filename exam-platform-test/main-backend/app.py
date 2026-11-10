"""Main backend (the service under test). Simple in-memory exam API."""
import os, secrets, time
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Exam main backend")
DURATION_S = int(os.getenv("EXAM_DURATION_S", "7200"))

sessions, tokens, by_candidate = {}, {}, {}
QUESTIONS = [{"id": f"q{i}", "type": "mcq", "prompt": f"Question {i}", "marks": 1,
              "options": ["A", "B", "C", "D"]} for i in range(1, 21)]


def get_session(sid: str, authorization: str | None):
    tok = (authorization or "").removeprefix("Bearer ")
    if tokens.get(tok) != sid:
        raise HTTPException(403, "forbidden")
    return sessions[sid]


class Join(BaseModel):
    candidate_id: str


class Answer(BaseModel):
    answer: str
    version: int = 1


@app.get("/")
@app.get("/health")
async def health():
    return {"ok": True, "active_sessions": len(sessions)}


@app.post("/v1/exams/{exam_id}/join")
async def join(exam_id: str, body: Join):
    if body.candidate_id in by_candidate:  # re-entry -> same session/deadline
        s = sessions[by_candidate[body.candidate_id]]
    else:
        sid, tok = secrets.token_hex(8), secrets.token_hex(16)
        s = {"id": sid, "token": tok, "deadline_at": time.time() + DURATION_S,
             "answers": {}, "submitted": False, "events": 0}
        sessions[sid], tokens[tok], by_candidate[body.candidate_id] = s, sid, sid
    return {"session_id": s["id"], "token": s["token"], "deadline_at": s["deadline_at"]}


@app.get("/v1/sessions/{sid}/exam")
async def get_exam(sid: str, authorization: str | None = Header(None)):
    get_session(sid, authorization)
    return {"questions": QUESTIONS}  # never includes answer key


@app.put("/v1/sessions/{sid}/answers/{qid}")
async def save_answer(sid: str, qid: str, body: Answer, authorization: str | None = Header(None)):
    s = get_session(sid, authorization)
    if s["submitted"] or time.time() > s["deadline_at"]:
        raise HTTPException(409, "attempt closed")
    cur = s["answers"].get(qid)
    if cur is None or body.version > cur["version"]:
        s["answers"][qid] = {"answer": body.answer, "version": body.version}
    return {"saved": True, "version": s["answers"][qid]["version"]}


@app.post("/v1/sessions/{sid}/events")
async def event(sid: str, body: dict, authorization: str | None = Header(None)):
    get_session(sid, authorization)["events"] += 1
    return {"stored": True}


@app.post("/v1/sessions/{sid}/submit")
async def submit(sid: str, authorization: str | None = Header(None)):
    s = get_session(sid, authorization)
    s["submitted"] = True  # idempotent
    return {"submitted": True, "answers_stored": len(s["answers"])}
