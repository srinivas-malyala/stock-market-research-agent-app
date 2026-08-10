CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
  id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE, display_name TEXT,
  last_visit_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS watchlists (
  id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL DEFAULT 'Primary', is_default BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS watchlist_tickers (
  watchlist_id BIGINT NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE, ticker TEXT NOT NULL,
  added_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_viewed_at TIMESTAMPTZ, PRIMARY KEY(watchlist_id, ticker)
);
CREATE TABLE IF NOT EXISTS companies (
  ticker TEXT PRIMARY KEY, name TEXT, description TEXT, sector TEXT, industry TEXT, sic_code TEXT,
  primary_exchange TEXT, market_cap NUMERIC, weighted_shares_outstanding NUMERIC, currency TEXT,
  homepage_url TEXT, list_date DATE, filing_excerpt TEXT, earnings_call_summary TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb, synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS price_snapshots (
  id BIGSERIAL PRIMARY KEY, ticker TEXT NOT NULL, captured_at TIMESTAMPTZ NOT NULL,
  open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, vwap NUMERIC, volume NUMERIC,
  previous_close NUMERIC, change_amount NUMERIC, change_percent NUMERIC,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb, UNIQUE(ticker, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_price_ticker_time ON price_snapshots(ticker, captured_at DESC);
CREATE TABLE IF NOT EXISTS news_articles (
  id TEXT PRIMARY KEY, ticker TEXT NOT NULL, title TEXT NOT NULL, description TEXT, author TEXT,
  article_url TEXT, publisher TEXT, keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
  sentiment TEXT, sentiment_reasoning TEXT, published_at TIMESTAMPTZ, full_text TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb, synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_news_ticker_time ON news_articles(ticker, published_at DESC);
CREATE TABLE IF NOT EXISTS research_notes (
  id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL, title TEXT NOT NULL, note_text TEXT NOT NULL, thesis_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS analysis_reports (
  id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL, thesis TEXT, tickers TEXT[] NOT NULL DEFAULT '{}', report_text TEXT NOT NULL,
  source_context JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_embeddings (
  id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_id TEXT NOT NULL, ticker TEXT,
  chunk_index INTEGER NOT NULL, chunk_text TEXT NOT NULL, content_hash TEXT NOT NULL,
  embedding vector(384) NOT NULL, model_name TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(source_type, source_id, chunk_index, model_name)
);
CREATE INDEX IF NOT EXISTS idx_research_embeddings_hnsw ON research_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_research_embeddings_ticker ON research_embeddings(ticker);
CREATE TABLE IF NOT EXISTS stock_research_mcp_traces (
  trace_id BIGSERIAL PRIMARY KEY, session_id UUID NOT NULL, server_name TEXT NOT NULL,
  tool_name TEXT NOT NULL, tool_parameters JSONB, user_email TEXT, started_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL DEFAULT now(), duration_ms INTEGER,
  status TEXT NOT NULL CHECK(status IN ('success','error')), result JSONB, error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_stock_trace_time ON stock_research_mcp_traces(started_at DESC);
