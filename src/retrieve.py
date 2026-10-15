#!/usr/bin/env python3
"""Search the local FAQ index.

Usage:
    python src/retrieve.py "how much does it cost?"
    python src/retrieve.py "can you do whatsapp" --k 3
"""
import argparse
import json

import numpy as np

import embed
from embed import CHUNKS_PATH, EMBED_MODEL, INDEX_DIR, embed_texts, file_hash


class Retriever:
    def __init__(self):
        meta_path = INDEX_DIR / "meta.json"
        if not meta_path.exists():
            raise SystemExit("No index found. Run: python src/embed.py")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta["model"] != EMBED_MODEL:
            raise SystemExit(f"Index was built with {meta['model']} but EMBED_MODEL is {EMBED_MODEL}. "
                             "Re-run python src/embed.py (or set EMBED_MODEL to match).")
        if CHUNKS_PATH.exists() and meta["chunks_hash"] != file_hash(CHUNKS_PATH):
            print("WARNING: faq_chunks.jsonl changed since the index was built. Re-run python src/embed.py\n")
        self.chunks = meta["chunks"]
        self.vectors = np.load(INDEX_DIR / "embeddings.npy")

    def search(self, query: str, k: int = 5, min_score: float = 0.0):
        """Return up to k results as [{'score': float, 'chunk': dict}], best first."""
        q = embed_texts([query])[0]
        scores = self.vectors @ q                      # cosine (vectors are normalized)
        order = np.argsort(-scores)[:k]
        return [{"score": float(scores[i]), "chunk": self.chunks[i]}
                for i in order if scores[i] >= min_score]


def format_context(results) -> str:
    """Build the text that replaces {{RETRIEVED_CHUNKS}} in the system prompt."""
    if not results:
        return "(No relevant information was found.)"
    blocks = []
    for r in results:
        c = r["chunk"]
        blocks.append(
            f'<chunk id="{c["chunk_id"]}" category="{c["category"]}" '
            f'requires_escalation="{str(c["requires_escalation"]).lower()}">\n'
            f'Question: {c["question"]}\nAnswer: {c["answer"]}\n</chunk>'
        )
    return "\n\n".join(blocks)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()
    for r in Retriever().search(args.query, k=args.k):
        c = r["chunk"]
        print(f'{r["score"]:.3f}  {c["knowledge_id"]}  [{c["category"]}]  {c["question"]}')