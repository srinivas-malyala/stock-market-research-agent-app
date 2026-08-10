"""Business adapter: Massive calls, normalization, persistence and research logic."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests
from psycopg2.extras import Json

import lakebase
from massive_client import MassiveClient

TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_client: MassiveClient | None = None
_model = None


def client() -> MassiveClient:
    global _client
    if _client is None:
        _client = MassiveClient()
    return _client


def _symbol(value: str) -> str:
    symbol = (value or "").strip().upper()
    if not TICKER.fullmatch(symbol):
        raise ValueError("Ticker must be a valid 1-10 character U.S. market symbol.")
    return symbol


def _error(error: Exception) -> dict:
    if isinstance(error, ValueError):
        return {"status": "error", "error_code": "invalid_request", "message": str(error)}
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else None
        message = "Massive could not return market data for that request."
        if status in (401, 403):
            message = "Massive denied this endpoint. Check the API secret and subscription entitlement."
        elif status == 404:
            message = "No Massive market data was found for that ticker."
        elif status == 429:
            message = "The Massive API rate limit was reached. Try again shortly."
        return {"status": "error", "error_code": f"massive_http_{status or 'error'}", "message": message}
    return {"status": "error", "error_code": "research_error", "message": "The research request could not be completed."}


def _upsert_company(ticker: str, data: dict) -> None:
    address = data.get("address") or {}
    lakebase.write("""
      INSERT INTO companies(ticker,name,description,sector,industry,sic_code,primary_exchange,market_cap,
        weighted_shares_outstanding,currency,homepage_url,list_date,payload,synced_at)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
      ON CONFLICT(ticker) DO UPDATE SET name=excluded.name,description=excluded.description,
        sector=excluded.sector,industry=excluded.industry,sic_code=excluded.sic_code,
        primary_exchange=excluded.primary_exchange,market_cap=excluded.market_cap,
        weighted_shares_outstanding=excluded.weighted_shares_outstanding,currency=excluded.currency,
        homepage_url=excluded.homepage_url,list_date=excluded.list_date,payload=excluded.payload,synced_at=now()
    """, (ticker, data.get("name"), data.get("description"), data.get("sic_description"),
          data.get("sic_description"), data.get("sic_code"), data.get("primary_exchange"),
          data.get("market_cap"), data.get("weighted_shares_outstanding"), data.get("currency_name"),
          data.get("homepage_url"), data.get("list_date"), Json({**data, "normalized_address": address})))


def _bar(row: dict) -> dict:
    ts = datetime.fromtimestamp(row["t"] / 1000, tz=timezone.utc)
    return {"date": ts.date().isoformat(), "captured_at": ts.isoformat(), "open": row.get("o"),
            "high": row.get("h"), "low": row.get("l"), "close": row.get("c"),
            "vwap": row.get("vw"), "volume": row.get("v"), "transactions": row.get("n")}


def _save_bars(ticker: str, raw: list[dict]) -> None:
    for index, row in enumerate(raw):
        current = _bar(row)
        previous = raw[index - 1].get("c") if index else None
        change = (row.get("c") - previous) if previous and row.get("c") is not None else None
        pct = (change / previous * 100) if previous and change is not None else None
        lakebase.write("""
          INSERT INTO price_snapshots(ticker,captured_at,open,high,low,close,vwap,volume,previous_close,change_amount,change_percent,payload)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT(ticker,captured_at) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,
            close=excluded.close,vwap=excluded.vwap,volume=excluded.volume,previous_close=excluded.previous_close,
            change_amount=excluded.change_amount,change_percent=excluded.change_percent,payload=excluded.payload
        """, (ticker, current["captured_at"], current["open"], current["high"], current["low"], current["close"],
              current["vwap"], current["volume"], previous, change, pct, Json(row)))


def _news_record(ticker: str, item: dict) -> dict:
    insights = [i for i in item.get("insights", []) if i.get("ticker") == ticker]
    insight = insights[0] if insights else {}
    publisher = item.get("publisher") or {}
    return {"id": item.get("id") or hashlib.sha256(f"{ticker}:{item.get('article_url')}".encode()).hexdigest(),
            "ticker": ticker, "title": item.get("title"), "description": item.get("description"),
            "author": item.get("author"), "article_url": item.get("article_url"),
            "publisher": publisher.get("name") if isinstance(publisher, dict) else str(publisher),
            "keywords": item.get("keywords") or [], "sentiment": insight.get("sentiment"),
            "sentiment_reasoning": insight.get("sentiment_reasoning"), "published_at": item.get("published_utc")}


def _save_news(records: list[dict], raw_by_id: dict[str, dict]) -> None:
    for row in records:
        lakebase.write("""
          INSERT INTO news_articles(id,ticker,title,description,author,article_url,publisher,keywords,sentiment,
            sentiment_reasoning,published_at,payload,synced_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
          ON CONFLICT(id) DO UPDATE SET title=excluded.title,description=excluded.description,author=excluded.author,
            article_url=excluded.article_url,publisher=excluded.publisher,keywords=excluded.keywords,
            sentiment=excluded.sentiment,sentiment_reasoning=excluded.sentiment_reasoning,
            published_at=excluded.published_at,payload=excluded.payload,synced_at=now()
        """, (row["id"], row["ticker"], row["title"] or "Untitled", row["description"], row["author"],
              row["article_url"], row["publisher"], Json(row["keywords"]), row["sentiment"],
              row["sentiment_reasoning"], row["published_at"], Json(raw_by_id[row["id"]])))


def get_stock_performance(ticker: str, lookback_days: int = 30) -> dict:
    try:
        symbol = _symbol(ticker); days = min(max(int(lookback_days), 2), 365)
        end = date.today(); start = end - timedelta(days=days + 8)
        raw = client().get_daily_bars(symbol, start.isoformat(), end.isoformat())
        if not raw: raise ValueError(f"No daily price bars were found for {symbol}.")
        _save_bars(symbol, raw)
        bars = [_bar(row) for row in raw]
        first, last = bars[0], bars[-1]
        change = last["close"] - first["close"]
        try:
            snapshot = client().get_snapshot(symbol)
            current = {"available": True, "price": (snapshot.get("lastTrade") or {}).get("p") or (snapshot.get("day") or {}).get("c"),
                       "today_change": snapshot.get("todaysChange"), "today_change_percent": snapshot.get("todaysChangePerc"),
                       "day": snapshot.get("day"), "previous_day": snapshot.get("prevDay"), "updated": snapshot.get("updated")}
        except requests.HTTPError as exc:
            current = {"available": False, "message": _error(exc)["message"], "fallback": "latest daily aggregate"}
        return {"status": "success", "ticker": symbol, "lookback_days": days, "as_of": last["date"],
                "current_snapshot": current, "latest": last, "period_start_close": first["close"], "change_amount": round(change, 4),
                "change_percent": round(change / first["close"] * 100, 2),
                "period_high": max(x["high"] for x in bars), "period_low": min(x["low"] for x in bars), "daily_bars": bars}
    except Exception as error: return _error(error)


def get_company_research(ticker: str, news_limit: int = 10, include_fundamentals: bool = True) -> dict:
    try:
        symbol = _symbol(ticker); details = client().get_ticker_details(symbol)
        if not details: raise ValueError(f"No company profile was found for {symbol}.")
        _upsert_company(symbol, details)
        raw_news = client().get_news(symbol, min(max(int(news_limit), 1), 50))
        news = [_news_record(symbol, item) for item in raw_news]
        _save_news(news, {record["id"]: raw for record, raw in zip(news, raw_news)})
        try:
            filings = client().get_filings(symbol, 10)
        except requests.HTTPError as exc:
            filings = {"available": False, "message": _error(exc)["message"], "items": []}
        fundamentals: dict[str, Any] = {"available": False, "message": "Fundamentals were not requested."}
        if include_fundamentals:
            try:
                fundamentals = {"available": True, "income_statements": client().get_income_statements(symbol),
                                "balance_sheets": client().get_balance_sheets(symbol)}
            except requests.HTTPError as exc:
                fundamentals = {"available": False, "message": _error(exc)["message"]}
        profile = {key: details.get(key) for key in ("ticker", "name", "description", "sic_code", "sic_description",
                   "primary_exchange", "market_cap", "weighted_shares_outstanding", "currency_name", "homepage_url", "list_date")}
        return {"status": "success", "ticker": symbol, "profile": profile, "fundamentals": fundamentals,
                "filings": filings, "news": news, "source": "Massive Stocks API"}
    except Exception as error: return _error(error)


def compare_stocks(tickers: list[str], lookback_days: int = 30) -> dict:
    if not isinstance(tickers, list) or not 2 <= len(tickers) <= 5:
        return _error(ValueError("Provide between 2 and 5 tickers to compare."))
    comparisons, errors = [], []
    for ticker in tickers:
        result = get_stock_performance(ticker, lookback_days)
        if result.get("status") == "success":
            comparisons.append({k: result[k] for k in ("ticker", "as_of", "latest", "change_percent", "period_high", "period_low")})
        else: errors.append({"ticker": ticker, "message": result.get("message")})
    if not comparisons: return {"status": "error", "error_code": "no_comparisons", "message": "No ticker could be compared.", "errors": errors}
    return {"status": "success", "lookback_days": lookback_days, "comparisons": comparisons, "errors": errors}


def _user(email: str) -> int:
    email = (email or "").strip().lower()
    if "@" not in email: raise ValueError("A valid user email is required.")
    return lakebase.write("INSERT INTO users(email) VALUES(%s) ON CONFLICT(email) DO UPDATE SET updated_at=now() RETURNING id", (email,), True)["id"]


def _watchlist(user_id: int, name: str) -> int:
    return lakebase.write("""INSERT INTO watchlists(user_id,name,is_default) VALUES(%s,%s,true)
      ON CONFLICT(user_id,name) DO UPDATE SET name=excluded.name RETURNING id""", (user_id, (name or "Primary").strip()), True)["id"]


def get_watchlist(user_email: str, watchlist_name: str = "Primary") -> dict:
    try:
        user_id = _user(user_email); watchlist_id = _watchlist(user_id, watchlist_name)
        rows = lakebase.query("""SELECT wt.ticker,wt.added_at,c.name,c.description,c.market_cap,
          p.close,p.change_percent,p.captured_at FROM watchlist_tickers wt LEFT JOIN companies c ON c.ticker=wt.ticker
          LEFT JOIN LATERAL (SELECT close,change_percent,captured_at FROM price_snapshots WHERE ticker=wt.ticker ORDER BY captured_at DESC LIMIT 1) p ON true
          WHERE wt.watchlist_id=%s ORDER BY wt.added_at""", (watchlist_id,))
        return {"status": "success", "watchlist": watchlist_name, "tickers": rows}
    except Exception as error: return _error(error)


def update_watchlist(user_email: str, ticker: str, action: str, watchlist_name: str = "Primary") -> dict:
    try:
        symbol = _symbol(ticker); verb = (action or "").lower()
        if verb not in ("add", "remove"): raise ValueError("action must be 'add' or 'remove'.")
        watchlist_id = _watchlist(_user(user_email), watchlist_name)
        if verb == "add":
            lakebase.write("INSERT INTO watchlist_tickers(watchlist_id,ticker) VALUES(%s,%s) ON CONFLICT DO NOTHING", (watchlist_id, symbol))
        else:
            lakebase.write("DELETE FROM watchlist_tickers WHERE watchlist_id=%s AND ticker=%s", (watchlist_id, symbol))
        result = get_watchlist(user_email, watchlist_name); result.update({"action": verb, "ticker": symbol})
        return result
    except Exception as error: return _error(error)


def save_research_note(user_email: str, ticker: str, title: str, note_text: str, thesis_tags: list[str] | None = None) -> dict:
    try:
        symbol = _symbol(ticker)
        if not title.strip() or not note_text.strip(): raise ValueError("title and note_text are required.")
        row = lakebase.write("""INSERT INTO research_notes(user_id,ticker,title,note_text,thesis_tags)
          VALUES(%s,%s,%s,%s,%s) RETURNING id,created_at""", (_user(user_email), symbol, title.strip(), note_text.strip(), Json(thesis_tags or [])), True)
        return {"status": "success", "note_id": row["id"], "ticker": symbol, "created_at": row["created_at"]}
    except Exception as error: return _error(error)


def save_analysis_report(user_email: str, title: str, thesis: str, tickers: list[str], report_text: str, source_context: dict | None = None) -> dict:
    try:
        symbols = [_symbol(t) for t in tickers]
        if not title.strip() or not report_text.strip(): raise ValueError("title and report_text are required.")
        row = lakebase.write("""INSERT INTO analysis_reports(user_id,title,thesis,tickers,report_text,source_context)
          VALUES(%s,%s,%s,%s,%s,%s) RETURNING id,created_at""", (_user(user_email), title.strip(), thesis, symbols, report_text.strip(), Json(source_context or {})), True)
        return {"status": "success", "report_id": row["id"], "tickers": symbols, "created_at": row["created_at"]}
    except Exception as error: return _error(error)


def semantic_research(query: str, top_k: int = 5, tickers: list[str] | None = None) -> dict:
    global _model
    try:
        text = (query or "").strip(); limit = min(max(int(top_k), 1), 20)
        if not text: raise ValueError("A non-empty semantic research query is required.")
        if _model is None:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        vector = "[" + ",".join(str(float(x)) for x in _model.encode(text, normalize_embeddings=True)) + "]"
        symbols = [_symbol(t) for t in tickers] if tickers else None
        rows = lakebase.query("""SELECT source_type,source_id,ticker,chunk_text,
          1-(embedding <=> %s::vector) AS similarity FROM research_embeddings
          WHERE (%s::text[] IS NULL OR ticker=ANY(%s::text[])) ORDER BY embedding <=> %s::vector LIMIT %s""",
          (vector, symbols, symbols, vector, limit))
        return {"status": "success", "query": text, "matches": rows, "count": len(rows)}
    except Exception as error: return _error(error)


def get_notable_updates(user_email: str, move_threshold_percent: float = 5.0, mark_visited: bool = True) -> dict:
    try:
        user_id = _user(user_email)
        rows = lakebase.query("SELECT last_visit_at FROM users WHERE id=%s", (user_id,))
        since = rows[0]["last_visit_at"] or datetime.now(timezone.utc) - timedelta(days=7)
        moves = lakebase.query("""SELECT DISTINCT ON(p.ticker) p.ticker,p.close,p.change_percent,p.captured_at
          FROM price_snapshots p JOIN watchlist_tickers wt ON wt.ticker=p.ticker JOIN watchlists w ON w.id=wt.watchlist_id
          WHERE w.user_id=%s AND p.captured_at>%s AND abs(coalesce(p.change_percent,0)) >= %s
          ORDER BY p.ticker,p.captured_at DESC""", (user_id, since, abs(float(move_threshold_percent))))
        news = lakebase.query("""SELECT n.ticker,n.title,n.sentiment,n.published_at,n.article_url FROM news_articles n
          JOIN watchlist_tickers wt ON wt.ticker=n.ticker JOIN watchlists w ON w.id=wt.watchlist_id
          WHERE w.user_id=%s AND n.published_at>%s ORDER BY n.published_at DESC LIMIT 50""", (user_id, since))
        if mark_visited: lakebase.write("UPDATE users SET last_visit_at=now(),updated_at=now() WHERE id=%s", (user_id,))
        return {"status": "success", "since": since, "notable_price_moves": moves, "new_articles": news,
                "price_move_threshold_percent": abs(float(move_threshold_percent))}
    except Exception as error: return _error(error)
