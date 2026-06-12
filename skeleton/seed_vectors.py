"""
TransitFlow — pgvector policy document seeder
=============================================

Run once after PostgreSQL is up and seeded::

    python skeleton/seed_vectors.py

Workflow:
  1. Ensure ``policy_documents`` columns exist (``ensure_policy_schema``).
  2. Load pre-chunked policies from ``train-mock-data/policy_chunks.json``.
  3. Embed each chunk with the active LLM provider (Ollama or Gemini).
  4. Upsert rows via ``store_policy_document`` (ON CONFLICT on ``chunk_id``).

Re-run after editing policy JSON or switching embedding models.
Ollama default: ``nomic-embed-text`` (768 dimensions).
"""

import json
import os
import sys
import time

sys.path.insert(0, ".")

import psycopg2

from skeleton.config import PG_DSN
from skeleton.llm_provider import llm
from databases.relational.queries import store_policy_document

_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)


def _load(filename):
    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def _text(data):
    return json.dumps(data, indent=2, ensure_ascii=False)


def build_documents():
    return _load("policy_chunks.json")


def ensure_policy_schema() -> None:
    """Upgrade policy_documents on existing DBs without a full docker reset."""
    alters = [
        "ALTER TABLE policy_documents ADD COLUMN IF NOT EXISTS chunk_id VARCHAR(150)",
        "ALTER TABLE policy_documents ADD COLUMN IF NOT EXISTS document_type VARCHAR(50)",
        "ALTER TABLE policy_documents ADD COLUMN IF NOT EXISTS policy_id VARCHAR(100)",
        "ALTER TABLE policy_documents ADD COLUMN IF NOT EXISTS metadata JSONB",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_chunk_id ON policy_documents(chunk_id)",
        "CREATE INDEX IF NOT EXISTS idx_policy_metadata ON policy_documents USING GIN (metadata)",
    ]
    with psycopg2.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            for stmt in alters:
                cur.execute(stmt)
        conn.commit()
    print("✓ policy_documents schema ready\n")


def seed():
    ensure_policy_schema()
    documents = build_documents()
    print(f"📄 Embedding {len(documents)} policy documents using {llm.chat_provider}...\n")

    for i, doc in enumerate(documents):
        print(f"  [{i+1}/{len(documents)}] Embedding: {doc['chunk_id']}")

        try:
            embedding = llm.embed(doc["content"])

            if len(embedding) != llm.embed_dim:
                print(f"    ⚠️  Unexpected embedding dim: {len(embedding)} (expected {llm.embed_dim})")
                print(f"    Update GEMINI_EMBED_DIM or OLLAMA_EMBED_DIM in skeleton/config.py")
                sys.exit(1)

            doc_id = store_policy_document(
                chunk_id=doc["chunk_id"],
                title=doc["title"],
                category=doc.get("document_type", "policy"),
                document_type=doc.get("document_type", "policy"),
                policy_id=doc.get("policy_id")
                or (doc.get("metadata") or {}).get("policy_id"),
                content=doc["content"],
                metadata=doc.get("metadata", {}),
                embedding=embedding,
                source_file=doc.get("source_file", ""),
            )
            print(f"    ✓ Stored as document id={doc_id}")

        except Exception as e:
            print(f"    ✗ Failed: {e}")
            raise

        if llm.chat_provider == "gemini" and i < len(documents) - 1:
            time.sleep(0.5)

    print(f"\n✅ All {len(documents)} policy documents embedded and stored.")
    print("   Test with a similarity search:")
    print("   >>> from skeleton.llm_provider import llm")
    print("   >>> from databases.relational.queries import query_policy_vector_search")
    print("   >>> results = query_policy_vector_search(llm.embed('can I get a refund for a delay?'))")
    print("   >>> print(results[0]['title'])")


if __name__ == "__main__":
    seed()
