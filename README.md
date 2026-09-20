# Smart Trade Analyzer

Opportunity scanner for Bitget markets. Enter a symbol (e.g. `BTCUSDT`), pick a timeframe and market
(Spot / Futures), press **Analyze** -- the app runs the full analytical pipeline
(data quality -> features -> regime -> setup -> confluence -> entry -> risk -> quality gate)
and shows the resulting decision (`LONG` / `SHORT` / `WAIT` / `NO TRADE`) with its reasons and warnings.

> **Disclaimer:** analysis/education tool only -- not financial advice. Thresholds in the engine are
> provisional and have not been validated by a backtest yet. Do not trade real money based on this output alone.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Opens at http://localhost:8501. No API key is needed (public Bitget market-data endpoints only).

## Deploy on Streamlit Community Cloud

Repository: this repo - Branch: `main` - Main file path: `app.py` - Python: 3.12.

## Tests

```bash
pip install pytest
python -m pytest -q
```

Project notes and phase history: see [HANDOFF.md](HANDOFF.md).
