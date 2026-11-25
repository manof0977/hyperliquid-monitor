"""Central configuration. All thresholds are configurable via env / Settings page."""
import os
from dataclasses import dataclass, field

BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://fapi.binance.com")
BINANCE_WS_URL = os.getenv("BINANCE_WS_URL", "wss://fstream.binance.com")
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "data", "scanner.db"))
SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC", "60"))
SCAN_LIMIT = int(os.getenv("SCAN_LIMIT", "60"))          # symbols getting full MTF analysis
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", "300"))       # candles per TF per symbol
TICKER_POLL_SEC = int(os.getenv("TICKER_POLL_SEC", "5"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d", "1w"]
TF_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}


@dataclass
class Thresholds:
    high_quality: int = 85
    strong: int = 75
    developing: int = 60
    weak: int = 40
    # opportunity-score gates for signal states
    confirmed_min: int = 70
    developing_min: int = 50
    watch_min: int = 35


@dataclass
class Settings:
    scan_limit: int = SCAN_LIMIT
    scan_interval_sec: int = SCAN_INTERVAL_SEC
    kline_limit: int = KLINE_LIMIT
    swing_left: int = 3
    swing_right: int = 3
    equal_tolerance_atr: float = 0.25
    ob_displacement_atr: float = 1.0
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    thresholds: Thresholds = field(default_factory=Thresholds)
    backtest_fee_pct: float = 0.05   # per side, %
    backtest_slippage_pct: float = 0.02


settings = Settings()
