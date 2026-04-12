import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 10))

HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"

HEADERS = {
    "Content-Type": "application/json"
}