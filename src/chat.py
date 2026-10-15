#!/usr/bin/env python3
"""GRIT AI chatbot: retrieval + OpenAI chat + two tools (v1).

Usage:
    python src/chat.py                 # interactive chat
    python src/chat.py --debug         # also show retrieved chunks and tool calls
    python src/chat.py --models        # list chat models available to your API key

Environment:
    OPENAI_API_KEY   required (or in .env at the repo root)
    CHAT_MODEL       chat model name (default: gpt-5.4-mini). Check --models for what your
                     account can use. Avoid the GPT-4 family: it is scheduled to shut down
                     on the OpenAI API on 2026-10-23.

Reads:  prompts/system_prompt.md, prompts/config.json, kb/index/*
Writes: data/leads.jsonl, data/escalations.jsonl, data/notifications.jsonl (+ emails the team, see notify.py)
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import embed
import notify
from retrieve import Retriever, format_context

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "system_prompt.md"
CONFIG_PATH = ROOT / "prompts" / "config.json"
DATA_DIR = ROOT / "data"
TOP_K = 5
MAX_TOOL_ROUNDS = 4
FALLBACK_REPLY = ("Sorry, I'm having trouble right now. Please contact our team directly "
                  "and they'll be happy to help.")


def chat_model() -> str:
    embed.load_env()
    return os.getenv("CHAT_MODEL", "gpt-5.4-mini")


TOOLS = [
    {"type": "function", "function": {
        "name": "capture_lead",
        "description": ("Save a prospect's details so the GRIT AI sales team can follow up. "
                        "Call ONLY after the person has actually given you their name, a way to "
                        "contact them, and a description of their business problem. Never invent values."),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
            "contact": {"type": "string", "description": "Email address or phone number"},
            "problem": {"type": "string", "description": "Their business problem or goal"},
            "company": {"type": "string"},
            "existing_systems": {"type": "string"},
            "scale": {"type": "string"},
            "timeline": {"type": "string"},
        }, "required": ["name", "contact", "problem"]},
    }},
    {"type": "function", "function": {
        "name": "escalate_to_human",
        "description": ("Hand the conversation to a human on the GRIT AI team (they are notified by email). "
                        "Use when the person asks for a human, or for complaints, disputes, sensitive matters, "
                        "frustration, or when you cannot answer and they want a person. Include `contact` only "
                        "if the person has already shared an email address or phone number."),
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "One-line summary of what the person needs"},
            "reason": {"type": "string", "enum": [
                "human_requested", "complaint", "sensitive", "low_confidence",
                "buying_intent", "frustration", "other"]},
            "contact": {"type": "string", "description": "Email or phone, only if already shared"},
        }, "required": ["summary", "reason"]},
    }},
]


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    for key, val in cfg.items():
        if str(val).startswith("TODO"):
            print(f"NOTE: prompts/config.json -> {key} is still a placeholder; fill it in before going live.")
    return cfg


def build_system_prompt(context: str, cfg: dict) -> str:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)           # drop author comments
    for key, val in cfg.items():
        text = text.replace("{{" + key + "}}", str(val))
    text = text.replace("{{RETRIEVED_CHUNKS}}", context)
    left = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if left:
        print(f"WARNING: unfilled placeholders in system prompt: {sorted(set(left))}")
    return text.strip()


def retrieval_query(history, user_msg: str) -> str:
    """Short follow-ups ('and how long?') borrow the previous user turn for retrieval."""
    if history and len(user_msg.split()) < 6:
        prev = [m["content"] for m in history if m["role"] == "user"]
        if prev:
            return prev[-1] + " " + user_msg
    return user_msg


def run_tool(name: str, args: dict, dry_run: bool, context=None, meta=None) -> dict:
    """Save to a local file (source of truth), then email the team in the background."""
    record = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **args}
    if not dry_run:
        DATA_DIR.mkdir(exist_ok=True)
        fname = {"capture_lead": "leads.jsonl", "escalate_to_human": "escalations.jsonl"}[name]
        with open(DATA_DIR / fname, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if name == "capture_lead":
            notify.notify_lead(args, context, meta)
        else:
            notify.notify_escalation(args, context, meta)
    if name == "capture_lead":
        return {"status": "saved",
                "note": "The GRIT AI team has been notified and will follow up. Do not promise a response time."}
    if args.get("contact"):
        return {"status": "queued", "note": "The team has been notified and has the customer's contact details."}
    return {"status": "queued",
            "note": ("The team has been notified, but you have NO way to reach this customer yet. Tell them a team "
                     "member will follow up, then ask for their name and an email or phone number (once they give "
                     "it, call capture_lead), or point them to the contact channel in your instructions.")}


def respond(retriever, client, history, user_msg, cfg=None, dry_run=False, meta=None):
    """One chat turn. Mutates `history`. Returns reply plus debug info."""
    cfg = cfg if cfg is not None else load_config()
    results = retriever.search(retrieval_query(history, user_msg), k=TOP_K)
    messages = ([{"role": "system", "content": build_system_prompt(format_context(results), cfg)}]
                + history + [{"role": "user", "content": user_msg}])

    context = history + [{"role": "user", "content": user_msg}]   # what the team sees in alert emails
    tools_called, reply = [], None
    for _ in range(MAX_TOOL_ROUNDS):
        resp = client.chat.completions.create(model=chat_model(), messages=messages, tools=TOOLS)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            reply = (msg.content or "").strip()
            break
        messages.append({
            "role": "assistant", "content": msg.content,
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                           for tc in msg.tool_calls],
        })
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = run_tool(tc.function.name, args, dry_run, context, meta)
            except Exception as exc:                     # bad args / unknown tool
                args, result = {}, {"status": "error", "error": str(exc)}
            tools_called.append({"name": tc.function.name, "args": args})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
    if reply is None:
        reply = FALLBACK_REPLY

    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": reply})
    return {
        "reply": reply,
        "tools": tools_called,
        "retrieved": [(r["chunk"]["knowledge_id"], round(r["score"], 3)) for r in results],
    }


def make_client():
    embed.load_env()
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set.")
    from openai import OpenAI
    return OpenAI()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--models", action="store_true", help="list models available to your key")
    args = ap.parse_args()
    client = make_client()

    if args.models:
        ids = sorted(m.id for m in client.models.list().data
                     if m.id.startswith(("gpt-", "o", "chat")) and "embedding" not in m.id)
        print("\n".join(ids))
        return

    retriever, cfg, history = Retriever(), load_config(), []
    print(f"GRIT AI assistant (model: {chat_model()}). Type /reset to start over, /quit to exit.\n")
    while True:
        try:
            user = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user == "/quit":
            break
        if user == "/reset":
            history.clear()
            print("(conversation reset)\n")
            continue
        out = respond(retriever, client, history, user, cfg)
        if args.debug:
            print("  [retrieved]", ", ".join(f"{i} {s}" for i, s in out["retrieved"]))
            for t in out["tools"]:
                print("  [tool]", t["name"], json.dumps(t["args"], ensure_ascii=False))
        print(f"\nBot: {out['reply']}\n")


if __name__ == "__main__":
    main()