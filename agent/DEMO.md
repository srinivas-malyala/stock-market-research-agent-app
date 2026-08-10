# Agent evaluation scenarios

Capture the Agent Bricks tool trace and final answer for at least these flows:

1. **Price:** “How has AAPL performed over the last 30 days?” Expected tool:
   `get_stock_performance(ticker="AAPL", lookback_days=30)`.
2. **Comparison:** “Compare MSFT and AMZN over the last 90 days.” Expected tool:
   `compare_stocks` with both tickers and one shared window.
3. **Thesis/context:** “Which companies in my watchlist appear exposed to
   rising rates?” Expected tools: `get_watchlist`, `semantic_research`, followed
   by company research for the relevant tickers.
4. **Mutation:** “Add NVDA to my watchlist.” Expected tool:
   `update_watchlist(action="add")`; final answer lists the updated watchlist.
5. **Error:** ask for ticker `NOT_A_REAL_TICKER`. The agent must present a clean
   correction request and must not invent a company or price.

