"""Massive Stocks REST client used by the research adapter.

Credentials come from a Databricks secret in deployed apps. ``MASSIVE_API_KEY``
is accepted only to make local development and tests convenient.
"""
from __future__ import annotations

import base64
import os
from typing import Any, Iterator

import requests
from databricks.sdk import WorkspaceClient
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = os.getenv("MASSIVE_API_BASE_URL", "https://api.massive.com").rstrip("/")
SECRET_SCOPE = os.getenv("MASSIVE_SECRET_SCOPE", "massive")
SECRET_KEY = os.getenv("MASSIVE_SECRET_KEY", "api-key")


def _api_key() -> str:
    if os.getenv("MASSIVE_API_KEY"):
        return os.environ["MASSIVE_API_KEY"]
    secret = WorkspaceClient().secrets.get_secret(scope=SECRET_SCOPE, key=SECRET_KEY)
    return base64.b64decode(secret.value).decode("utf-8")


class MassiveClient:
    """Small authenticated client with retries and Massive pagination support."""

    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({"Authorization": f"Bearer {api_key or _api_key()}", "User-Agent": "stock-research-agent/1.0"})

    def get(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def paginated_get(self, path: str, params: dict[str, Any] | None = None, max_items: int = 500) -> Iterator[dict]:
        """Yield ``results`` across Massive ``next_url`` pages."""
        url: str | None = path
        query = dict(params or {})
        emitted = 0
        while url and emitted < max_items:
            data = self.get(url, query)
            query = None
            for item in data.get("results", []):
                yield item
                emitted += 1
                if emitted >= max_items:
                    return
            url = data.get("next_url")

    def get_ticker_details(self, ticker: str) -> dict:
        return self.get(f"/v3/reference/tickers/{ticker}").get("results", {})

    def get_latest_price(self, ticker: str) -> dict:
        return self.get(f"/v2/aggs/ticker/{ticker}/prev")

    def get_snapshot(self, ticker: str) -> dict:
        """Return Massive's most recent single-ticker market snapshot."""
        return self.get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}").get("ticker", {})

    def get_daily_bars(self, ticker: str, from_date: str, to_date: str, limit: int = 5000) -> list[dict]:
        data = self.get(
            f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}",
            {"adjusted": "true", "sort": "asc", "limit": limit},
        )
        return data.get("results", [])

    def get_news(self, ticker: str, limit: int = 20, published_utc_gte: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"ticker": ticker, "limit": min(max(limit, 1), 1000), "order": "desc", "sort": "published_utc"}
        if published_utc_gte:
            params["published_utc.gte"] = published_utc_gte
        return self.get("/v2/reference/news", params).get("results", [])

    def get_income_statements(self, ticker: str, limit: int = 4) -> list[dict]:
        """Return recent reported income statements (availability depends on plan)."""
        data = self.get("/stocks/financials/v1/income-statements", {"tickers": ticker, "limit": limit, "sort": "filing_date.desc"})
        return data.get("results", [])

    def get_balance_sheets(self, ticker: str, limit: int = 4) -> list[dict]:
        data = self.get("/stocks/financials/v1/balance-sheets", {"tickers": ticker, "limit": limit, "sort": "filing_date.desc"})
        return data.get("results", [])

    def get_filings(self, ticker: str, limit: int = 10) -> list[dict]:
        """Discover recent SEC EDGAR filings and their source URLs."""
        data = self.get("/stocks/filings/vX/index", {"ticker": ticker, "limit": limit, "sort": "filing_date.desc"})
        return data.get("results", [])
