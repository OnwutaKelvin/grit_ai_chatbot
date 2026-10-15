#!/usr/bin/env python3
"""Email notifications to the GRIT AI team (SMTP).

Used automatically by the chatbot's tools:
    capture_lead       -> "New lead" email
    escalate_to_human  -> "Human handoff requested" email (includes the conversation so far)

Test your email settings:
    python src/notify.py --test

Settings (environment variables or a .env file in the repo root; see .env.example):
    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD
    SMTP_SECURITY   starttls (default) | ssl | none
    EMAIL_FROM      sender address (defaults to SMTP_USER)
    NOTIFY_TO       who gets the alerts; comma-separated for several people
    NOTIFY_MAX_PER_HOUR   safety cap on alert emails (default 30)

Emails are sent in the background so the customer never waits on them, and failures never
break the chat. Every attempt is logged to data/notifications.jsonl. The lead/escalation
is always saved to data/leads.jsonl / data/escalations.jsonl first, so nothing is lost
if email is down.
"""
import argparse
import json
import os
import re
import smtplib
import ssl
import threading
import time
from collections import deque
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import embed

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEDUPE_SECONDS = 600
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

_lock = threading.Lock()
_sent_times = deque()
_recent = {}
_warned = False


def settings() -> dict:
    embed.load_env()
    s = {k: os.getenv(k, "").strip() for k in
         ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_SECURITY", "EMAIL_FROM", "NOTIFY_TO")}
    s["SMTP_PORT"] = int(s["SMTP_PORT"] or 587)
    s["SMTP_SECURITY"] = (s["SMTP_SECURITY"] or "starttls").lower()
    s["EMAIL_FROM"] = s["EMAIL_FROM"] or s["SMTP_USER"]
    s["recipients"] = [x.strip() for x in s["NOTIFY_TO"].split(",") if x.strip()]
    return s


def configured(s=None) -> bool:
    s = s or settings()
    return bool(s["SMTP_HOST"] and s["EMAIL_FROM"] and s["recipients"])


def clean_header(text: str, limit: int = 150) -> str:
    return re.sub(r"[\r\n]+", " ", str(text)).strip()[:limit]


def send_email(subject: str, body: str, reply_to: str = None):
    """Send one email synchronously. Raises on failure."""
    s = settings()
    if not configured(s):
        raise RuntimeError("Email is not configured (need SMTP_HOST, NOTIFY_TO, and EMAIL_FROM or SMTP_USER).")
    msg = EmailMessage()
    msg["Subject"] = clean_header(subject)
    msg["From"] = s["EMAIL_FROM"]
    msg["To"] = ", ".join(s["recipients"])
    if reply_to and EMAIL_RE.fullmatch(reply_to.strip()):
        msg["Reply-To"] = reply_to.strip()
    msg.set_content(body)

    ctx = ssl.create_default_context()
    if s["SMTP_SECURITY"] == "ssl":
        server = smtplib.SMTP_SSL(s["SMTP_HOST"], s["SMTP_PORT"], timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(s["SMTP_HOST"], s["SMTP_PORT"], timeout=20)
    with server:
        server.ehlo()
        if s["SMTP_SECURITY"] == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
        if s["SMTP_USER"] and s["SMTP_PASSWORD"]:
            server.login(s["SMTP_USER"], s["SMTP_PASSWORD"])
        server.send_message(msg)


def log(record: dict):
    DATA_DIR.mkdir(exist_ok=True)
    record = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
    with _lock:
        with open(DATA_DIR / "notifications.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _worker(kind, subject, body, reply_to):
    last = None
    for attempt in (1, 2):                       # one retry for transient network errors
        try:
            send_email(subject, body, reply_to)
            log({"kind": kind, "status": "sent", "subject": clean_header(subject)})
            return
        except Exception as exc:
            last = f"{type(exc).__name__}: {str(exc)[:200]}"
            time.sleep(2)
    log({"kind": kind, "status": "failed", "subject": clean_header(subject), "error": last})


def dispatch(kind: str, key: str, subject: str, body: str, reply_to: str = None) -> str:
    """Queue an email in the background. Returns queued | not_configured | skipped_duplicate | skipped_rate_limit."""
    global _warned
    if not configured():
        if not _warned:
            print("NOTE: email notifications are not configured (see .env.example); alerts are only saved to data/.")
            _warned = True
        log({"kind": kind, "status": "not_configured", "subject": clean_header(subject)})
        return "not_configured"

    now = time.time()
    cap = int(os.getenv("NOTIFY_MAX_PER_HOUR", "30"))
    with _lock:
        while _sent_times and now - _sent_times[0] > 3600:
            _sent_times.popleft()
        if now - _recent.get(key, 0) < DEDUPE_SECONDS:
            status = "skipped_duplicate"
        elif len(_sent_times) >= cap:
            status = "skipped_rate_limit"
        else:
            status = "queued"
            _sent_times.append(now)
            _recent[key] = now
    if status != "queued":
        log({"kind": kind, "status": status, "subject": clean_header(subject)})
        return status
    threading.Thread(target=_worker, args=(kind, subject, body, reply_to)).start()
    return "queued"


# --------------------------------------------------------------- formatting ---
def _field(value, limit=1000) -> str:
    return str(value).strip()[:limit] if value not in (None, "") else "-"


def transcript(context, n: int = 8) -> str:
    lines = []
    for m in (context or [])[-n:]:
        who = "Customer" if m["role"] == "user" else "Bot"
        lines.append(f"{who}: {_field(m.get('content'), 600)}")
    return "\n\n".join(lines) or "(no conversation available)"


def notify_lead(args: dict, context=None, meta=None) -> str:
    meta = meta or {}
    who = _field(args.get("name"), 60)
    subject = f"[GRIT AI chatbot] New lead: {who} ({_field(args.get('company') or args.get('problem'), 60)})"
    body = (
        "A prospect asked to be contacted by the GRIT AI team.\n\n"
        f"Name:             {_field(args.get('name'))}\n"
        f"Contact:          {_field(args.get('contact'))}\n"
        f"Company:          {_field(args.get('company'))}\n"
        f"Problem / goal:   {_field(args.get('problem'))}\n"
        f"Existing systems: {_field(args.get('existing_systems'))}\n"
        f"Scale:            {_field(args.get('scale'))}\n"
        f"Timeline:         {_field(args.get('timeline'))}\n\n"
        f"--- Recent conversation ---\n{transcript(context)}\n\n"
        f"--- Details ---\nSession: {_field(meta.get('session_id'))}\n"
        "Sent automatically by the GRIT AI chatbot. Reply to this email to reach the prospect "
        "if their contact is an email address.\n"
    )
    key = "lead:" + _field(args.get("contact"), 120).lower()
    return dispatch("lead", key, subject, body, reply_to=args.get("contact"))


def notify_escalation(args: dict, context=None, meta=None) -> str:
    meta = meta or {}
    reason = _field(args.get("reason"), 40)
    has_contact = bool(args.get("contact"))
    subject = f"[GRIT AI chatbot] Human requested: {reason}" + ("" if has_contact else " (no contact details yet)")
    body = (
        "A customer chatting with the GRIT AI bot needs a person.\n\n"
        f"Reason:   {reason}\n"
        f"Summary:  {_field(args.get('summary'))}\n"
        f"Contact:  {_field(args.get('contact'))}\n\n"
        + ("" if has_contact else
           "NOTE: the customer has not shared contact details yet. The bot was told to ask for them; "
           "if they do, you'll get a separate 'New lead' email.\n\n")
        + f"--- Recent conversation ---\n{transcript(context)}\n\n"
        f"--- Details ---\nSession: {_field(meta.get('session_id'))}\n"
        "Sent automatically by the GRIT AI chatbot.\n"
    )
    key = f"esc:{_field(meta.get('session_id'), 64)}:{reason}"
    return dispatch("escalation", key, subject, body, reply_to=args.get("contact"))


# ---------------------------------------------------------------------- CLI ---
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="send a test email now")
    if not ap.parse_args().test:
        return ap.print_help()
    s = settings()
    print(f"Server: {s['SMTP_HOST'] or '(not set)'}:{s['SMTP_PORT']} ({s['SMTP_SECURITY']})  "
          f"User: {s['SMTP_USER'] or '(none)'}  From: {s['EMAIL_FROM'] or '(not set)'}  To: {s['recipients'] or '(not set)'}")
    try:
        send_email("[GRIT AI chatbot] Test email", "If you can read this, chatbot email alerts are working.")
        print("Success: test email sent. Check the inbox (and spam folder) of the NOTIFY_TO address(es).")
    except smtplib.SMTPAuthenticationError:
        print("FAILED: the server rejected the login. Gmail and Microsoft 365 usually need an app password "
              "(not your normal password), or SMTP sign-in may be disabled by your admin.")
    except (smtplib.SMTPConnectError, TimeoutError, OSError) as exc:
        print(f"FAILED: couldn't connect ({exc}). Check SMTP_HOST, SMTP_PORT and SMTP_SECURITY "
              "(587 + starttls, or 465 + ssl) and that your network allows outgoing SMTP.")
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()