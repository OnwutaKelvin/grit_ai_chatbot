#!/usr/bin/env python3
"""GRIT AI chatbot web server (v1).

Usage:
    python src/server.py                 # then open http://127.0.0.1:8000
    PORT=9000 python src/server.py       # PowerShell: $env:PORT = "9000"

Endpoints:
    GET  /            chat page (web/index.html)
    POST /chat        {message, session_id?} -> {session_id, message_id, reply, handoff, lead_saved}
    POST /feedback    {message_id, rating: "up"|"down", comment?}
    GET  /health

Environment (all optional):
    ALLOWED_ORIGINS    comma-separated site origins allowed to call the API
                       (default "*" for local testing; set to your real site before going live)
    RATE_LIMIT_PER_MIN messages per IP per minute (default 20)
    HOST, PORT         bind address (default 127.0.0.1:8000)

Logs (data/): conversations.jsonl (emails/phone numbers redacted), feedback.jsonl,
plus leads.jsonl and escalations.jsonl written by the chat tools.
"""
import json
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import chat
from retrieve import Retriever

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
DATA_DIR = chat.DATA_DIR

MAX_MESSAGE_CHARS = 1000
MAX_HISTORY_MESSAGES = 20          # keep the last 20 messages (10 turns) as context
MAX_TURNS_PER_SESSION = 40
SESSION_TTL_SECONDS = 3600
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MIN", "20"))
LIMIT_REPLY = ("We've reached the limit for this conversation. Please start a new chat, "
               "or contact our team directly.")

app = FastAPI(title="GRIT AI chatbot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

_lock = threading.Lock()
_runtime = {}
_sessions = {}
_hits = defaultdict(deque)


# ---------------------------------------------------------------- helpers ---
def runtime():
    """Load retriever, OpenAI client, and prompt config once, on first use."""
    with _lock:
        if not _runtime:
            _runtime.update(retriever=Retriever(), client=chat.make_client(), cfg=chat.load_config())
        return _runtime


def redact(text: str) -> str:
    """Mask emails and phone-like numbers before writing to conversation logs."""
    text = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[email]", text or "")
    return re.sub(r"\+?\d[\d\s().-]{7,}\d", "[phone]", text)


def append_jsonl(name: str, record: dict):
    DATA_DIR.mkdir(exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _lock:
        with open(DATA_DIR / name, "a", encoding="utf-8") as f:
            f.write(line)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_session(session_id: Optional[str]):
    now = time.time()
    with _lock:
        for k in [k for k, v in _sessions.items() if now - v["last"] > SESSION_TTL_SECONDS]:
            del _sessions[k]
        if not session_id or session_id not in _sessions:
            session_id = uuid.uuid4().hex
            _sessions[session_id] = {"history": [], "last": now, "turns": 0}
        sess = _sessions[session_id]
        sess["last"] = now
        return session_id, sess


def rate_limited(ip: str) -> bool:
    now = time.time()
    with _lock:
        q = _hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return True
        q.append(now)
        return False


def client_ip(request: Request) -> str:
    # Behind a proxy/load balancer, configure it to pass X-Forwarded-For and trust it here.
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


# ----------------------------------------------------------------- models ---
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    session_id: Optional[str] = Field(default=None, max_length=64)


class FeedbackIn(BaseModel):
    message_id: str = Field(max_length=64)
    rating: Literal["up", "down"]
    comment: Optional[str] = Field(default=None, max_length=500)


# -------------------------------------------------------------- endpoints ---
@app.get("/health")
def health():
    return {"status": "ok", "model": chat.chat_model()}


@app.get("/")
def index():
    page = WEB_DIR / "index.html"
    if not page.exists():
        raise HTTPException(404, "web/index.html not found")
    return FileResponse(page)


@app.post("/chat")
def chat_endpoint(body: ChatIn, request: Request):
    if rate_limited(client_ip(request)):
        raise HTTPException(429, "Too many messages. Please wait a moment and try again.")
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Message is empty.")

    session_id, sess = get_session(body.session_id)
    message_id = uuid.uuid4().hex[:12]
    if sess["turns"] >= MAX_TURNS_PER_SESSION:
        return {"session_id": session_id, "message_id": message_id, "reply": LIMIT_REPLY,
                "handoff": False, "lead_saved": False, "limit_reached": True}

    rt, started, error = runtime(), time.time(), None
    try:
        out = chat.respond(rt["retriever"], rt["client"], sess["history"], message, rt["cfg"],
                           meta={"session_id": session_id, "message_id": message_id})
    except Exception as exc:                          # never show raw errors to customers
        out = {"reply": chat.FALLBACK_REPLY, "tools": [], "retrieved": []}
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    sess["turns"] += 1
    del sess["history"][:-MAX_HISTORY_MESSAGES]

    tool_names = [t["name"] for t in out["tools"]]
    append_jsonl("conversations.jsonl", {
        "time": now_iso(), "session_id": session_id, "message_id": message_id,
        "user": redact(message), "reply": redact(out["reply"]),
        "tools": tool_names,                       # names only: tool args contain personal data
        "retrieved": out["retrieved"], "model": chat.chat_model(),
        "latency_ms": int((time.time() - started) * 1000), "error": error,
    })
    return {"session_id": session_id, "message_id": message_id, "reply": out["reply"],
            "handoff": "escalate_to_human" in tool_names,
            "lead_saved": "capture_lead" in tool_names}


@app.post("/feedback")
def feedback_endpoint(body: FeedbackIn):
    append_jsonl("feedback.jsonl", {"time": now_iso(), "message_id": body.message_id,
                                    "rating": body.rating, "comment": redact(body.comment or "")})
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))