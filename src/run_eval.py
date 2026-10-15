#!/usr/bin/env python3
"""Run the 50 test questions through the full chatbot and print a scorecard.

Usage:
    python src/run_eval.py                    # all tests
    python src/run_eval.py --only T23,T24     # specific tests
    python src/run_eval.py --category security
    python src/run_eval.py --show-all         # print every reply, not just failures

Each test checks: retrieval (expected FAQ entries in top-5), must_include,
must_not_include, tool behavior, and whether a human handoff was offered.
These are quick automated checks; failed ones need a human look at the reply.
Full replies are saved to eval/results.json. Tools run in dry-run mode (no files written).
"""
import argparse
import collections
import json
import re
from pathlib import Path

import chat
from retrieve import Retriever

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "eval" / "test_questions.json"
RESULTS = ROOT / "eval" / "results.json"
HANDOFF_WORDS = ["team", "human", "person", "colleague", "specialist", "representative",
                 "sales", "connect you", "reach out", "contact us", "get in touch"]


def norm(s: str) -> str:
    return s.lower().replace("\u2019", "'").replace("\u2018", "'")


def contains(reply: str, term: str) -> bool:
    term = norm(term)
    if re.fullmatch(r"[a-z0-9' ]+", term):            # plain words: whole-word match
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", reply) is not None
    return term in reply                               # symbols like "$", "50%": substring


def grade(test, out):
    reply = norm(out["reply"])
    called = {t["name"] for t in out["tools"]}
    checks = {}

    if test["expected_ids"]:
        got = {i for i, _ in out["retrieved"]}
        checks["retrieval"] = any(e in got for e in test["expected_ids"])
    checks["includes"] = all(contains(reply, t) for t in test["must_include"])
    checks["excludes"] = not any(contains(reply, t) for t in test["must_not_include"])

    exp = test["expected_tool"]
    if exp == "escalate_to_human":
        checks["tool"] = "escalate_to_human" in called
    elif exp == "capture_lead":
        # Single turn: details haven't been given yet, so the bot must ASK, not save invented data
        checks["tool"] = "capture_lead" not in called
    else:
        checks["tool"] = "capture_lead" not in called
    if test["should_offer_human"]:
        checks["offers_human"] = ("escalate_to_human" in called) or any(w in reply for w in HANDOFF_WORDS)
    return checks


def main(only, category, show_all):
    tests = json.loads(TESTS.read_text(encoding="utf-8"))
    if only:
        tests = [t for t in tests if t["id"] in only]
    if category:
        tests = [t for t in tests if t["category"] == category]

    retriever, client, cfg = Retriever(), chat.make_client(), chat.load_config()
    print(f"Running {len(tests)} tests with model {chat.chat_model()} ...\n")
    results, by_cat = [], collections.defaultdict(lambda: [0, 0])

    for t in tests:
        try:
            out = chat.respond(retriever, client, [], t["question"], cfg, dry_run=True)
            checks = grade(t, out)
        except Exception as exc:                       # keep going if one call fails
            out, checks = {"reply": f"ERROR: {exc}", "tools": [], "retrieved": []}, {"error": False}
        passed = all(checks.values())
        by_cat[t["category"]][0] += passed
        by_cat[t["category"]][1] += 1
        results.append({"id": t["id"], "category": t["category"], "question": t["question"],
                        "passed": passed, "checks": checks, **out})
        mark = "PASS" if passed else "FAIL"
        print(f'{mark}  {t["id"]} [{t["category"]}] {t["question"]}')
        if not passed or show_all:
            failed = [k for k, v in checks.items() if not v]
            if failed:
                print(f"      failed checks: {failed}")
            print(f"      tools: {[x['name'] for x in out['tools']] or 'none'}")
            print("      reply: " + out["reply"].replace("\n", " ")[:420])

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    total = sum(r["passed"] for r in results)
    print(f"\n=== Scorecard: {total}/{len(results)} passed ({total/max(len(results),1):.0%}) ===")
    for cat, (p, n) in sorted(by_cat.items()):
        print(f"  {cat:<14} {p}/{n}")
    print(f"\nFull replies saved to {RESULTS}")
    print("Tip: a failed phrase check isn't always a bad answer. Read the reply before changing anything.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--category", default="")
    ap.add_argument("--show-all", action="store_true")
    a = ap.parse_args()
    main({x.strip() for x in a.only.split(",") if x.strip()}, a.category, a.show_all)