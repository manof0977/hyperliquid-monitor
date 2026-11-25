# Binance Crypto Intelligence Scanner (USDT-M Perpetual Futures)

A real-time quantitative technical-analysis & Smart Money Concepts (SMC) scanner for **Binance USDⓈ-M Perpetual Futures** built with **Python + FastAPI + Pandas/NumPy + SQLite** on the backend and **React + TypeScript + Vite + TradingView Lightweight Charts** on the frontend.

No Binance API key or trading credentials are required — all market data comes from Binance's public endpoints (`https://fapi.binance.com` and `wss://fstream.binance.com`).

---

## Architecture & Modules

```text
backend/app/
  config.py              # Configurable thresholds, scan cadence, fees & slippage
  db.py                  # SQLite (WAL mode) persistence: symbols, candles, watchlist, alerts, settings
  binance/client.py      # Public USDⓈ-M REST + combined WebSocket stream, X-MBX-USED-WEIGHT-1M limiter, gap recovery
  analysis/
    indicators.py        # EMA 20/50/100/200, RSI 14, MACD(12,26,9), ATR 14, Vol SMA 20, squeeze & divergence
    structure.py         # Non-repainting 3L/3R fractal swings (HH/HL/LH/LL) + closed-candle BOS & CHoCH
    smc.py               # Bullish/Bearish Order Blocks, 3-candle FVG, Liquidity Sweeps, EQH/EQL, Premium/Discount
    sr.py                # Swing S/R clusters, PDH/PDL, PWH/PWL, Supply/Demand zones, % & ATR distance
    scoring.py           # Dual 0-100 Technical Score & Opportunity Score (LONG & SHORT), Signal State Machine, Early Watch
    pipeline.py          # Multi-timeframe (5m, 15m, 1h, 4h, 1d, 1w) orchestrator
  scanner/engine.py      # Async background scanner, incremental candle cache, live WS updates
  alerts/telegram.py     # Optional Telegram push notifications with strict transition de-duplication
  backtest/engine.py     # Causal walk-forward bar-by-bar backtester (zero look-ahead, configurable fees/slippage)
  api/routes.py          # REST + /api/ws live terminal push endpoints
  main.py                # FastAPI app + SPA static serving for production deployments
frontend/src/
  components/SMCChart.tsx # Lightweight Charts + synchronized 2D canvas for OB/FVG rectangles, BOS lines & trade plan
  pages/                 # Dashboard, Market Scanner, Top Opportunities, Coin Analysis, Watchlist, Alerts, Backtesting, Settings
tests/test_analysis.py   # Pytest suite verifying non-repainting structure, SMC, dual scoring & causal backtesting
```

---

## Scoring & Signal States

1. **Technical Chart Score (0–100, independent LONG & SHORT):**
   - Market Structure (25) • SMC & Liquidity (25) • EMA & Trend (20) • Momentum & Volume (15) • Multi-Timeframe Agreement (15)
2. **Trade Opportunity Score (0–100, independent LONG & SHORT):**
   - Entry-Zone Proximity (25) • Setup Confirmation (25) • Structural Risk/Reward (25) • Invalidation Clarity (15) • Room to Target (10)
3. **Signal States:** `LONG WATCH`, `SHORT WATCH`, `LONG DEVELOPING`, `SHORT DEVELOPING`, `LONG CONFIRMED`, `SHORT CONFIRMED`, `INVALIDATED`, `NO SETUP` + pre-breakout **`EARLY WATCH`** radar.
   - A high score **never** automatically triggers `CONFIRMED`; confirmation requires closed-candle LTF BOS/CHoCH, proximity to a structural OB/FVG/S&R zone, and structural `R:R >= 1.5`.

---

## One-Click Launch on Your Own PC (Windows / Mac / Zero-Install Webpage)

Download the repo as a **ZIP** from GitHub (`Code` → `Download ZIP`), unzip it anywhere on your computer, and pick whichever way you prefer:

1. **Double-Click Windows App Mode (`run_scanner.bat`) — Recommended**
   - Double-click **`run_scanner.bat`**.
   - If Python 3.10+ is installed, it automatically sets up `.venv` on first run, starts the full FastAPI + SQLite + Backtesting engine, and pops open Chrome/Edge in standalone **App Window** mode (`--app=http://127.0.0.1:8000`) — no Node.js needed because the production UI is pre-built in `backend/static/`.
   - If Python is **not** installed on your PC, `run_scanner.bat` automatically opens **`Binance_Scanner_Standalone.html`** in Chrome instead!
   - Optional: double-click **`create_desktop_shortcut.bat`** once to place a **"Binance Intelligence Scanner"** icon right on your Windows Desktop.
2. **Zero-Install Single Webpage (`Binance_Scanner_Standalone.html`)**
   - Double-click **`Binance_Scanner_Standalone.html`** directly on any computer (Windows, Mac, Chromebook — zero installs). It opens in Chrome and connects straight to Binance's public REST + WebSocket APIs with live Lightweight Charts, 3L/3R swings, BOS/CHoCH, Order Blocks, FVGs, Sweeps, and Long/Short scoring.
3. **Mac / Linux One-Click (`run_scanner.command`)**
   - Double-click **`run_scanner.command`** on macOS (or `./run_scanner.command` on Linux).

---

## Running on Replit / Cloud

### Development Mode (Backend + Vite HMR Proxy)
```bash
chmod +x start.sh
./start.sh
```
- FastAPI runs on `0.0.0.0:8000`
- Vite Dev Server runs on `0.0.0.0:5173` and proxies `/api` + WebSockets (`/api/ws`) to FastAPI using relative browser URLs.

### Production / Reserved-VM Deployment
Replit `.replit` is configured with `deploymentTarget = "vm"` so the background scanner and SQLite database (`./data/scanner.db`) run persistently:
```bash
./start.sh prod
```

### Optional Telegram Secrets
Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in **Replit Secrets** (or `.env`), then toggle Telegram on in the **Settings** tab.

### Running Tests
```bash
pytest -v tests/test_analysis.py
```
