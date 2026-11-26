"""SQLite storage — WAL mode, batched candle upserts, watchlist, alerts log & settings."""
import asyncio
import json
import os
import aiosqlite
import pandas as pd
from app.config import DB_PATH

CANDLE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
_WRITE_LOCK = asyncio.Lock()


async def get_db() -> aiosqlite.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    db = await aiosqlite.connect(DB_PATH, timeout=15.0)
    await db.execute("PRAGMA busy_timeout=15000;")
    await db.execute("PRAGMA synchronous=NORMAL;")
    return db


async def init_db():
    async with _WRITE_LOCK:
        db = await get_db()
        try:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS symbols (
                    symbol TEXT PRIMARY KEY,
                    base_asset TEXT,
                    price_precision INTEGER,
                    tick_size REAL,
                    updated_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT NOT NULL,
                    tf TEXT NOT NULL,
                    open_time INTEGER NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume REAL,
                    close_time INTEGER,
                    PRIMARY KEY (symbol, tf, open_time)
                );
                CREATE TABLE IF NOT EXISTS watchlist (
                    symbol TEXT PRIMARY KEY,
                    added_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at INTEGER,
                    symbol TEXT,
                    status TEXT,
                    side TEXT,
                    price REAL,
                    message TEXT,
                    sent_telegram INTEGER DEFAULT 0,
                    dedup_key TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS kv_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            await db.commit()
        finally:
            await db.close()


async def upsert_candles(symbol: str, tf: str, rows: list[tuple]):
    if not rows:
        return
    async with _WRITE_LOCK:
        db = await get_db()
        try:
            await db.executemany(
                """INSERT INTO candles (symbol, tf, open_time, open, high, low, close, volume, close_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, tf, open_time) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume, close_time=excluded.close_time""",
                [(symbol, tf, *r) for r in rows],
            )
            await db.commit()
        finally:
            await db.close()


async def load_candles_df(symbol: str, tf: str, limit: int = 350) -> pd.DataFrame:
    db = await get_db()
    try:
        cur = await db.execute(
            """SELECT open_time, open, high, low, close, volume, close_time
               FROM candles WHERE symbol=? AND tf=?
               ORDER BY open_time DESC LIMIT ?""",
            (symbol, tf, limit),
        )
        rows = await cur.fetchall()
    finally:
        await db.close()
    if not rows:
        return pd.DataFrame(columns=CANDLE_COLS)
    rows.reverse()
    return pd.DataFrame(rows, columns=CANDLE_COLS)


async def last_candle_time(symbol: str, tf: str) -> int | None:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT MAX(open_time) FROM candles WHERE symbol=? AND tf=?", (symbol, tf)
        )
        row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None
    finally:
        await db.close()


async def save_symbols(items: list[dict], ts: int):
    if not items:
        return
    async with _WRITE_LOCK:
        db = await get_db()
        try:
            await db.executemany(
                """INSERT INTO symbols (symbol, base_asset, price_precision, tick_size, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                     base_asset=excluded.base_asset, price_precision=excluded.price_precision,
                     tick_size=excluded.tick_size, updated_at=excluded.updated_at""",
                [(x["symbol"], x["baseAsset"], x["pricePrecision"], x["tickSize"], ts) for x in items],
            )
            await db.commit()
        finally:
            await db.close()


async def get_watchlist() -> list[str]:
    db = await get_db()
    try:
        cur = await db.execute("SELECT symbol FROM watchlist ORDER BY added_at DESC")
        return [r[0] for r in await cur.fetchall()]
    finally:
        await db.close()


async def toggle_watchlist(symbol: str, add: bool, ts: int) -> list[str]:
    async with _WRITE_LOCK:
        db = await get_db()
        try:
            if add:
                await db.execute("INSERT OR IGNORE INTO watchlist(symbol, added_at) VALUES (?, ?)", (symbol, ts))
            else:
                await db.execute("DELETE FROM watchlist WHERE symbol=?", (symbol,))
            await db.commit()
        finally:
            await db.close()
    return await get_watchlist()


async def record_alert(ts: int, symbol: str, status: str, side: str, price: float, message: str, dedup_key: str, sent: bool) -> bool:
    async with _WRITE_LOCK:
        db = await get_db()
        try:
            try:
                await db.execute(
                    """INSERT INTO alerts (created_at, symbol, status, side, price, message, sent_telegram, dedup_key)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ts, symbol, status, side, price, message, 1 if sent else 0, dedup_key),
                )
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False
        finally:
            await db.close()


async def list_alerts(limit: int = 100) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id, created_at, symbol, status, side, price, message, sent_telegram FROM alerts ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [
            {"id": r[0], "created_at": r[1], "symbol": r[2], "status": r[3], "side": r[4],
             "price": r[5], "message": r[6], "sent_telegram": bool(r[7])}
            for r in rows
        ]
    finally:
        await db.close()


async def save_kv(key: str, val: dict):
    async with _WRITE_LOCK:
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO kv_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(val)),
            )
            await db.commit()
        finally:
            await db.close()


async def load_kv(key: str) -> dict | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT value FROM kv_settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return json.loads(row[0]) if row else None
    finally:
        await db.close()
