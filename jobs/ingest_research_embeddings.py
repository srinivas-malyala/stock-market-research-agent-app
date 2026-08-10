"""Chunk and embed stock research text, then write vectors with pg8000.

Reads profiles, filing excerpts, earnings-call summaries and news from Lakebase.
The write path deliberately uses pg8000 rather than Spark JDBC/psycopg2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pg8000.dbapi
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
import lakebase  # noqa: E402

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def chunks(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    clean = " ".join((text or "").split())
    if not clean: return
    start = 0
    while start < len(clean):
        end = min(start + size, len(clean))
        if end < len(clean):
            boundary = clean.rfind(" ", start + size // 2, end)
            if boundary > start: end = boundary
        yield clean[start:end]
        if end == len(clean): break
        start = max(end - overlap, start + 1)


def source_documents() -> list[dict]:
    rows = lakebase.query("""
      SELECT 'company_profile' source_type,ticker source_id,ticker,
        concat_ws(E'\n',name,description,'Sector: '||sector,'Industry: '||industry) content FROM companies
      UNION ALL SELECT 'filing_excerpt',ticker,ticker,filing_excerpt FROM companies WHERE nullif(filing_excerpt,'') IS NOT NULL
      UNION ALL SELECT 'earnings_call',ticker,ticker,earnings_call_summary FROM companies WHERE nullif(earnings_call_summary,'') IS NOT NULL
      UNION ALL SELECT 'news',id,ticker,concat_ws(E'\n',title,description,full_text,sentiment_reasoning) FROM news_articles
    """)
    return [row for row in rows if row.get("content")]


def pg8000_connection():
    parsed = urlparse(lakebase.get_lakebase_url())
    sslmode = parse_qs(parsed.query).get("sslmode", [""])[0]
    return pg8000.dbapi.connect(user=parsed.username, password=parsed.password, host=parsed.hostname,
                               port=parsed.port or 5432, database=parsed.path.lstrip("/"),
                               ssl_context=True if sslmode in ("require", "verify-ca", "verify-full") else None)


def main(batch_size: int = 100) -> int:
    lakebase.migrate()
    documents = source_documents()
    records = []
    for source in documents:
        for index, text in enumerate(chunks(source["content"])):
            digest = hashlib.sha256(text.encode()).hexdigest()
            record_id = hashlib.sha256(f"{source['source_type']}:{source['source_id']}:{index}:{MODEL_NAME}".encode()).hexdigest()
            records.append({**source, "id": record_id, "chunk_index": index, "chunk_text": text, "content_hash": digest})
    if not records:
        print("No research text is available to embed.")
        return 0
    model = SentenceTransformer(MODEL_NAME)
    embeddings = model.encode([row["chunk_text"] for row in records], batch_size=64, normalize_embeddings=True, show_progress_bar=True)
    connection = pg8000_connection()
    try:
        cursor = connection.cursor()
        sql = """INSERT INTO research_embeddings(id,source_type,source_id,ticker,chunk_index,chunk_text,content_hash,embedding,model_name)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s::vector,%s)
          ON CONFLICT(source_type,source_id,chunk_index,model_name) DO UPDATE SET ticker=excluded.ticker,
          chunk_text=excluded.chunk_text,content_hash=excluded.content_hash,embedding=excluded.embedding,created_at=now()"""
        for start in range(0, len(records), batch_size):
            values = []
            for row, vector in zip(records[start:start + batch_size], embeddings[start:start + batch_size]):
                values.append((row["id"], row["source_type"], str(row["source_id"]), row["ticker"], row["chunk_index"],
                               row["chunk_text"], row["content_hash"], json.dumps([float(x) for x in vector]), MODEL_NAME))
            cursor.executemany(sql, values)
            connection.commit()
        cursor.close()
    finally:
        connection.close()
    print(f"Embedded and upserted {len(records)} chunks from {len(documents)} documents.")
    return len(records)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    main(max(1, args.batch_size))

