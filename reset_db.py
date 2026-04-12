import asyncio
import aiosqlite
import time

async def reset():
    current_time = int(time.time() * 1000)
    async with aiosqlite.connect("wallets.db") as db:
        # Reset all wallet times to RIGHT NOW
        await db.execute(
            "UPDATE last_trades SET last_trade_time = ?",
            (current_time,)
        )
        await db.commit()
        print(f"Reset all wallets to current time: {current_time}")
        print("Bot will now only send NEW notifications from this point!")

asyncio.run(reset())
