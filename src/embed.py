#!/usr/bin/env python3
"""Embed the FAQ chunks with OpenAI and save a local vector index.

Usage:
    python src/embed.py

Reads:   kb/faq_chunks.jsonl            (produced by chunk.py)
Writes:  kb/index/embeddings.npy        (one normalized vector per chunk)
         kb/index/meta.json             (chunks + model name + content hash)

Needs OPENAI_API_KEY in the environment or in a .env file at the repo root.
Optional: set EMBED_MODEL to change the model (default text-embedding-3-small).
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CHUNKS_PATH = ROOT / "kb" / "faq_chunks.jsonl"
INDEX_DIR = ROOT / "kb" / "index"
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")


def load_env():
    """Load .env if python-dotenv is installed (optional)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def embed_texts(texts, model=EMBED_MODEL, batch_size=96):
    """Return an (n, dim) float32 array of L2-normalized embeddings."""
    load_env()
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set. See the setup steps for how to set it.")
    from openai import OpenAI
    client = OpenAI()
    vectors = []
    for i in range(0, len(texts), batch_size):
        resp = client.embeddings.create(model=model, input=texts[i:i + batch_size])
        vectors.extend(d.embedding for d in resp.data)
    arr = np.array(vectors, dtype=np.float32)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    return arr


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_index():
    if not CHUNKS_PATH.exists():
        raise SystemExit(f"{CHUNKS_PATH} not found. Run chunk.py first.")
    chunks = [json.loads(line) for line in CHUNKS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"Embedding {len(chunks)} chunks with {EMBED_MODEL} ...")
    vectors = embed_texts([c["embed_text"] for c in chunks])

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(INDEX_DIR / "embeddings.npy", vectors)
    meta = {
        "model": EMBED_MODEL,
        "dim": int(vectors.shape[1]),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chunks_hash": file_hash(CHUNKS_PATH),   # lets retrieve.py detect a stale index
        "chunks": chunks,
    }
    (INDEX_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {vectors.shape[0]} vectors ({vectors.shape[1]} dims) to {INDEX_DIR}")


if __name__ == "__main__":
    build_index()