import aiosqlite
import time

DB_PATH = "wallets.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                address TEXT NOT NULL,
                label TEXT,
                UNIQUE(chat_id, address)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS last_trades (
                address TEXT PRIMARY KEY,
                last_trade_time INTEGER DEFAULT 0
            )
        """)
        await db.commit()


async def add_wallet(chat_id: int, address: str, label: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO wallets (chat_id, address, label) VALUES (?, ?, ?)",
                (chat_id, address.lower(), label)
            )
            await db.commit()

            current_time = int(time.time() * 1000)
            async with db.execute(
                "SELECT last_trade_time FROM last_trades WHERE address = ?",
                (address.lower(),)
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                await db.execute(
                    "INSERT INTO last_trades (address, last_trade_time) VALUES (?, ?)",
                    (address.lower(), current_time)
                )
                await db.commit()

            return True

        except aiosqlite.IntegrityError:
            return False


async def remove_wallet(chat_id: int, address: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM wallets WHERE chat_id = ? AND address = ?",
            (chat_id, address.lower())
        )
        await db.commit()
        removed = cursor.rowcount > 0

        if removed:
            async with db.execute(
                "SELECT COUNT(*) FROM wallets WHERE address = ?",
                (address.lower(),)
            ) as cursor2:
                row = await cursor2.fetchone()
                count = row[0] if row else 0

            if count == 0:
                await db.execute(
                    "DELETE FROM last_trades WHERE address = ?",
                    (address.lower(),)
                )
                await db.commit()

        return removed


async def get_wallets_by_chat(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT address, label FROM wallets WHERE chat_id = ?",
            (chat_id,)
        ) as cursor:
            return await cursor.fetchall()


async def get_wallets_with_labels_by_chat(chat_id: int):
    """Get all wallets for a chat with their labels"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT address, label FROM wallets WHERE chat_id = ?",
            (chat_id,)
        ) as cursor:
            return await cursor.fetchall()


async def get_all_wallets():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT address, chat_id, label FROM wallets"
        ) as cursor:
            return await cursor.fetchall()


async def is_wallet_monitored(address: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM wallets WHERE address = ?",
            (address.lower(),)
        ) as cursor:
            row = await cursor.fetchone()
            return (row[0] if row else 0) > 0


async def get_last_trade_time(address: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT last_trade_time FROM last_trades WHERE address = ?",
            (address.lower(),)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def update_last_trade_time(address: str, timestamp: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO last_trades (address, last_trade_time)
            VALUES (?, ?)
            ON CONFLICT(address) DO UPDATE SET last_trade_time = ?
        """, (address.lower(), timestamp, timestamp))
        await db.commit()
