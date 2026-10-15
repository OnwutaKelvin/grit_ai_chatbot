"""Summarize chatbot usage from data/*.jsonl and show what to fix next.

Usage:
    python src/report.py
    python src/report.py --threshold 0.45    # flag answers whose best FAQ match scored below this
"""
import argparse
import collections
import json
import statistics as st
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def read(name):
    path = DATA / name
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main(threshold: float):
    convs, fb = read("conversations.jsonl"), read("feedback.jsonl")
    leads, esc = read("leads.jsonl"), read("escalations.jsonl")
    if not convs:
        print("No conversations logged yet. Chat with the bot first (python src/server.py).")
        return

    sessions = {c["session_id"] for c in convs}
    lat = [c["latency_ms"] for c in convs]
    errors = [c for c in convs if c.get("error")]
    print(f"=== Usage ({convs[0]['time'][:10]} to {convs[-1]['time'][:10]}) ===")
    print(f"Sessions: {len(sessions)}   Messages: {len(convs)}   "
          f"Avg latency: {st.mean(lat)/1000:.1f}s   Slowest: {max(lat)/1000:.1f}s   Errors: {len(errors)}")
    n_esc = sum("escalate_to_human" in c["tools"] for c in convs)
    n_lead = sum("capture_lead" in c["tools"] for c in convs)
    print(f"Human handoffs: {n_esc} ({n_esc/len(convs):.0%} of messages)   Leads captured: {n_lead}   "
          f"(files: {len(esc)} escalations, {len(leads)} leads)")

    notes = read("notifications.jsonl")
    if notes:
        counts = collections.Counter(n["status"] for n in notes)
        print("Alert emails: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
        for n in [n for n in notes if n["status"] == "failed"][-3:]:
            print(f'  FAILED {n["time"]}: {n["subject"]} -> {n.get("error", "")}')
        if counts.get("not_configured"):
            print("  WARNING: some alerts were not emailed because email is not configured (see .env.example).")

    up = sum(f["rating"] == "up" for f in fb)
    down = [f for f in fb if f["rating"] == "down"]
    print(f"Feedback: {up} thumbs up, {len(down)} thumbs down")

    by_id = {c["message_id"]: c for c in convs}
    if down:
        print("\n=== Thumbs-down answers (fix these first) ===")
        for f in down[-10:]:
            c = by_id.get(f["message_id"])
            if c:
                print(f'  Q: {c["user"][:100]}\n  A: {c["reply"][:160].replace(chr(10), " ")}')
                if f["comment"]:
                    print(f'  Comment: {f["comment"]}')
                print()

    weak = sorted(((c["retrieved"][0][1], c) for c in convs if c["retrieved"] and c["retrieved"][0][1] < threshold),
                  key=lambda x: x[0])
    print(f"=== Weak matches (best FAQ score < {threshold}): candidates for new FAQ entries ===")
    if not weak:
        print("  none")
    for score, c in weak[:15]:
        print(f'  {score:.2f}  {c["user"][:90]}   (closest: {c["retrieved"][0][0]})')

    top = collections.Counter(c["retrieved"][0][0] for c in convs if c["retrieved"])
    print("\n=== Most-asked topics (best-matching FAQ entry) ===")
    for kid, n in top.most_common(8):
        print(f"  {n:>3}  {kid}")

    if errors:
        print("\n=== Errors ===")
        for c in errors[-5:]:
            print(f'  {c["time"]}  {c["error"]}')


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.40)
    main(ap.parse_args().threshold)