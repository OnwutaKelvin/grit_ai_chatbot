#!/usr/bin/env python3
"""Score retrieval quality on eval/test_questions.json (embedding calls only, no LLM).

Usage:
    python src/eval_retrieval.py            # top-5
    python src/eval_retrieval.py --k 3
    python src/eval_retrieval.py --verbose  # show top results for every question

Reports Hit@1 / Hit@3 / Hit@k for questions that have expected FAQ entries,
and the score range for questions with none (used to pick a "no match" threshold).
"""
import argparse
import json
import statistics as st
from pathlib import Path

from retrieve import Retriever

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "eval" / "test_questions.json"


def main(k: int, verbose: bool):
    tests = json.loads(TESTS.read_text(encoding="utf-8"))
    r = Retriever()
    answerable, unanswerable, misses = [], [], []

    for t in tests:
        results = r.search(t["question"], k=max(k, 5))
        ids = [x["chunk"]["knowledge_id"] for x in results]
        top = results[0]["score"] if results else 0.0
        if verbose:
            print(f'\n{t["id"]} [{t["category"]}] {t["question"]}')
            for x in results[:k]:
                mark = "*" if x["chunk"]["knowledge_id"] in t["expected_ids"] else " "
                print(f'  {mark} {x["score"]:.3f} {x["chunk"]["knowledge_id"]} {x["chunk"]["question"]}')
        if t["expected_ids"]:
            ranks = [ids.index(e) + 1 for e in t["expected_ids"] if e in ids]
            first = min(ranks) if ranks else None
            answerable.append({"id": t["id"], "first": first, "top": top})
            if first is None or first > 3:
                misses.append((t, ids[:3], first, top))
        else:
            unanswerable.append({"id": t["id"], "top": top})

    n = len(answerable)
    hit = lambda cutoff: sum(1 for a in answerable if a["first"] and a["first"] <= cutoff)
    print(f"\n=== Retrieval results ({n} questions with expected entries) ===")
    print(f"Hit@1: {hit(1)}/{n} ({hit(1)/n:.0%})   Hit@3: {hit(3)}/{n} ({hit(3)/n:.0%})   Hit@{k}: {hit(k)}/{n} ({hit(k)/n:.0%})")

    if misses:
        print("\nQuestions where the right entry was not in the top 3:")
        for t, got, first, top in misses:
            where = f"rank {first}" if first else f"not in top {max(k, 5)}"
            print(f'  {t["id"]} "{t["question"]}"')
            print(f'      expected {t["expected_ids"]} ({where}), got {got}, top score {top:.3f}')

    hit_scores = [a["top"] for a in answerable if a["first"] == 1]
    if hit_scores and unanswerable:
        un = [a["top"] for a in unanswerable]
        print("\n=== Top-1 score ranges (for choosing a no-match threshold) ===")
        print(f"Correct top-1 hits  : min {min(hit_scores):.3f}  median {st.median(hit_scores):.3f}")
        print(f"No-entry questions  : median {st.median(un):.3f}  max {max(un):.3f}  ({len(un)} questions)")
        print("If the ranges overlap, don't use a score cutoff; rely on the prompt's 'say you don't know' rule.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    main(args.k, args.verbose)