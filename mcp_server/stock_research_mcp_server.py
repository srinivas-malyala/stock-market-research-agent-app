"""FastMCP stock-market research server for Databricks Agent Bricks."""
from __future__ import annotations

import inspect
import json
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps

from fastmcp import FastMCP
from starlette.middleware import Middleware as ASGIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

import lakebase
import research_broker as broker

mcp = FastMCP("stock-market-research")
_identity: ContextVar[dict] = ContextVar("identity", default={})
_session: ContextVar[str] = ContextVar("session", default="")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        identity = _identity.set({"email": request.headers.get("x-forwarded-email") or request.headers.get("x-forwarded-user")})
        session = _session.set(str(uuid.uuid4()))
        try: return await call_next(request)
        finally:
            _identity.reset(identity); _session.reset(session)


def traced(function):
    """Persist a safe tool trace; tracing failures never fail the tool."""
    @wraps(function)
    def wrapper(*args, **kwargs):
        started = datetime.now(timezone.utc); timer = time.perf_counter()
        bound = inspect.signature(function).bind(*args, **kwargs); bound.apply_defaults()
        try:
            result = function(*args, **kwargs)
        except Exception:
            result = {"status": "error", "error_code": "tool_error", "message": "The research tool could not complete this request."}
        try:
            lakebase.write("""INSERT INTO stock_research_mcp_traces(session_id,server_name,tool_name,tool_parameters,
              user_email,started_at,duration_ms,status,result,error_message) VALUES(%s,'stock-market-research',%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s)""",
              (_session.get() or str(uuid.uuid4()), function.__name__, json.dumps(bound.arguments), _identity.get().get("email"),
               started, int((time.perf_counter()-timer)*1000), result.get("status", "success"), json.dumps(result, default=str),
               result.get("message") if result.get("status") == "error" else None))
        except Exception:
            pass
        return result
    return wrapper


@mcp.tool
@traced
def get_stock_performance(ticker: str, lookback_days: int = 30) -> dict:
    """Get real historical daily bars and summarize a ticker's recent performance.

    Args:
        ticker: U.S. equity ticker, such as AAPL.
        lookback_days: Calendar-day lookback from 2 through 365.
    Returns:
        Latest OHLCV, period return/high/low and underlying daily bars.
    """
    return broker.get_stock_performance(ticker, lookback_days)


@mcp.tool
@traced
def get_company_research(ticker: str, news_limit: int = 10, include_fundamentals: bool = True) -> dict:
    """Get a company profile, recent news and available reported fundamentals.

    Args:
        ticker: U.S. equity ticker.
        news_limit: Number of recent articles, 1-50.
        include_fundamentals: Request income statements and balance sheets.
    Returns:
        Normalized company, SEC filing links, news and fundamentals data with
        clear entitlement errors.
    """
    return broker.get_company_research(ticker, news_limit, include_fundamentals)


@mcp.tool
@traced
def compare_stocks(tickers: list[str], lookback_days: int = 30) -> dict:
    """Compare recent price action for two to five tickers on the same window.

    Args:
        tickers: Two to five U.S. equity tickers.
        lookback_days: Shared calendar-day comparison window.
    Returns:
        Like-for-like latest price, return, period high and low per ticker.
    """
    return broker.compare_stocks(tickers, lookback_days)


@mcp.tool
@traced
def get_watchlist(user_email: str, watchlist_name: str = "Primary") -> dict:
    """Read a user's watchlist with the latest locally synced price facts.

    Args:
        user_email: Verified end-user identity.
        watchlist_name: Named list, default Primary.
    Returns:
        Current membership and latest available stored company/price facts.
    """
    return broker.get_watchlist(user_email, watchlist_name)


@mcp.tool
@traced
def update_watchlist(user_email: str, ticker: str, action: str, watchlist_name: str = "Primary") -> dict:
    """Add or remove one ticker from a named user watchlist.

    Args:
        user_email: Stable user identity.
        ticker: U.S. equity ticker.
        action: Exactly ``add`` or ``remove``.
        watchlist_name: Watchlist name; defaults to Primary.
    """
    return broker.update_watchlist(user_email, ticker, action, watchlist_name)


@mcp.tool
@traced
def save_research_note(user_email: str, ticker: str, title: str, note_text: str, thesis_tags: list[str] | None = None) -> dict:
    """Persist a user's explicit research note tied to one ticker.

    Args:
        user_email: Verified end-user identity.
        ticker: U.S. equity ticker.
        title: Short note title.
        note_text: User-confirmed research note.
        thesis_tags: Optional thematic labels.
    Returns:
        New note ID and creation time.
    """
    return broker.save_research_note(user_email, ticker, title, note_text, thesis_tags)


@mcp.tool
@traced
def save_analysis_report(user_email: str, title: str, thesis: str, tickers: list[str], report_text: str, source_context: dict | None = None) -> dict:
    """Persist an analysis report only after the user asks to save it.

    Args:
        user_email: Verified end-user identity.
        title: Report title.
        thesis: Investing question or hypothesis.
        tickers: Tickers covered by the report.
        report_text: User-confirmed report body.
        source_context: Optional provenance metadata from earlier tool calls.
    Returns:
        New report ID and creation time.
    """
    return broker.save_analysis_report(user_email, title, thesis, tickers, report_text, source_context)


@mcp.tool
@traced
def semantic_research(query: str, top_k: int = 5, tickers: list[str] | None = None) -> dict:
    """Retrieve semantically relevant profile, filing, earnings and news chunks.

    Args:
        query: Natural-language investing thesis or research question.
        top_k: Number of cosine-ranked passages, 1-20.
        tickers: Optional ticker filter.
    """
    return broker.semantic_research(query, top_k, tickers)


@mcp.tool
@traced
def get_notable_updates(user_email: str, move_threshold_percent: float = 5.0, mark_visited: bool = True) -> dict:
    """Return notable watchlist price moves and new articles since last visit.

    Args:
        user_email: Verified end-user identity.
        move_threshold_percent: Absolute daily percentage threshold.
        mark_visited: Advance last-visit time after successful retrieval.
    Returns:
        Locally synced qualifying moves, articles, and the comparison timestamp.
    """
    return broker.get_notable_updates(user_email, move_threshold_percent, mark_visited)


if __name__ == "__main__":
    lakebase.migrate()
    mcp.run(transport="http", middleware=[ASGIMiddleware(RequestContextMiddleware)])
