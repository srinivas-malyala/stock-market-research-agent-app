from __future__ import annotations
import re
from flask import Flask, jsonify, render_template, request
import lakebase

app = Flask(__name__)

def email():
    return request.headers.get("X-Forwarded-Email") or "demo@example.com"

def user_id():
    row = lakebase.write("INSERT INTO users(email) VALUES(%s) ON CONFLICT(email) DO UPDATE SET updated_at=now() RETURNING id", (email(),), True)
    return row["id"]

@app.get("/")
def index(): return render_template("index.html", user_email=email())

@app.get("/healthz")
def health(): return {"status": "ok"}

@app.get("/api/overview")
def overview():
    uid = user_id()
    watch = lakebase.query("""SELECT wt.ticker,c.name,p.close,p.change_percent,p.captured_at FROM watchlists w
      JOIN watchlist_tickers wt ON wt.watchlist_id=w.id LEFT JOIN companies c ON c.ticker=wt.ticker
      LEFT JOIN LATERAL(SELECT close,change_percent,captured_at FROM price_snapshots WHERE ticker=wt.ticker ORDER BY captured_at DESC LIMIT 1)p ON true
      WHERE w.user_id=%s ORDER BY wt.added_at""", (uid,))
    news = lakebase.query("""SELECT n.ticker,n.title,n.sentiment,n.published_at,n.article_url FROM news_articles n
      JOIN watchlist_tickers wt ON wt.ticker=n.ticker JOIN watchlists w ON w.id=wt.watchlist_id
      WHERE w.user_id=%s ORDER BY n.published_at DESC LIMIT 12""", (uid,))
    notes = lakebase.query("SELECT id,ticker,title,note_text,created_at FROM research_notes WHERE user_id=%s ORDER BY created_at DESC LIMIT 8", (uid,))
    reports = lakebase.query("SELECT id,title,tickers,thesis,created_at FROM analysis_reports WHERE user_id=%s ORDER BY created_at DESC LIMIT 8", (uid,))
    activity = lakebase.query("SELECT tool_name,status,started_at,duration_ms FROM stock_research_mcp_traces WHERE user_email=%s ORDER BY started_at DESC LIMIT 10", (email(),))
    return jsonify({"user": email(), "watchlist": watch, "news": news, "notes": notes, "reports": reports, "activity": activity})

@app.post("/api/watchlist")
def add_ticker():
    ticker = (request.json.get("ticker") or "").strip().upper(); uid = user_id()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", ticker):
        return {"status":"error", "message":"Enter a valid U.S. ticker."}, 400
    row = lakebase.write("INSERT INTO watchlists(user_id,name,is_default) VALUES(%s,'Primary',true) ON CONFLICT(user_id,name) DO UPDATE SET name=excluded.name RETURNING id", (uid,), True)
    lakebase.write("INSERT INTO watchlist_tickers(watchlist_id,ticker) VALUES(%s,%s) ON CONFLICT DO NOTHING", (row["id"], ticker))
    return {"status":"success", "ticker":ticker}, 201

@app.delete("/api/watchlist/<ticker>")
def remove_ticker(ticker):
    lakebase.write("DELETE FROM watchlist_tickers USING watchlists WHERE watchlist_tickers.watchlist_id=watchlists.id AND watchlists.user_id=%s AND watchlist_tickers.ticker=%s", (user_id(), ticker.upper()))
    return {"status":"success"}

if __name__ == "__main__": app.run(host="0.0.0.0", port=8000)
