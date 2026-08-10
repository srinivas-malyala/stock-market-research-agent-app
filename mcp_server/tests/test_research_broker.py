from datetime import date
from unittest.mock import Mock
import research_broker as broker

def test_invalid_ticker_returns_clean_error():
    result = broker.get_stock_performance("not a ticker!", 30)
    assert result["status"] == "error"
    assert result["error_code"] == "invalid_request"

def test_performance_calculates_return(monkeypatch):
    fake = Mock()
    fake.get_daily_bars.return_value = [
        {"t": 1704067200000, "o": 99, "h": 101, "l": 98, "c": 100, "v": 10},
        {"t": 1704153600000, "o": 100, "h": 111, "l": 99, "c": 110, "v": 20},
    ]
    fake.get_snapshot.return_value = {"lastTrade": {"p": 110}, "todaysChangePerc": 1.2}
    monkeypatch.setattr(broker, "client", lambda: fake)
    monkeypatch.setattr(broker, "_save_bars", lambda *args: None)
    result = broker.get_stock_performance("aapl", 30)
    assert result["status"] == "success"
    assert result["ticker"] == "AAPL"
    assert result["change_percent"] == 10.0

def test_compare_requires_two_tickers():
    result = broker.compare_stocks(["AAPL"], 30)
    assert result["status"] == "error"
