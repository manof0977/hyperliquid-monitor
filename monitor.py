import aiohttp
import asyncio
import logging
from datetime import datetime
from config import HYPERLIQUID_API, HEADERS, CHECK_INTERVAL
from database import get_all_wallets, get_last_trade_time, update_last_trade_time, is_wallet_monitored, get_wallets_by_chat

logger = logging.getLogger(__name__)


async def fetch_trades(session, address):
    payload = {"type": "userFills", "user": address}
    try:
        async with session.post(
            HYPERLIQUID_API,
            json=payload,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            return []
    except Exception as e:
        logger.error(f"Error fetching trades for {address}: {e}")
        return []


async def fetch_positions(session, address):
    payload = {"type": "clearinghouseState", "user": address}
    try:
        async with session.post(
            HYPERLIQUID_API,
            json=payload,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            return {}
    except Exception as e:
        logger.error(f"Error fetching positions for {address}: {e}")
        return {}


async def fetch_open_orders(session, address):
    payload = {"type": "openOrders", "user": address}
    try:
        async with session.post(
            HYPERLIQUID_API,
            json=payload,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            return []
    except Exception as e:
        logger.error(f"Error fetching orders for {address}: {e}")
        return []


def format_trade_message(trade, address, label):
    side = "🟢 LONG" if trade.get("side") == "B" else "🔴 SHORT"
    direction = "BUY" if trade.get("side") == "B" else "SELL"
    coin = trade.get("coin", "Unknown")
    size = trade.get("sz", "0")
    price = trade.get("px", "0")
    fee = trade.get("fee", "0")

    try:
        usd_value = float(size) * float(price)
        usd_formatted = f"${usd_value:,.2f}"
    except Exception:
        usd_formatted = "N/A"

    timestamp = trade.get("time", 0)
    trade_time = datetime.utcfromtimestamp(
        timestamp / 1000
    ).strftime('%Y-%m-%d %H:%M:%S UTC')

    wallet_display = label if label else f"{address[:6]}...{address[-4:]}"

    message = "🔔 *Trade Detected!*\n"
    message += f"👛 Wallet: `{wallet_display}`\n"
    message += "━━━━━━━━━━━━━━\n"
    message += f"{side} | {direction}\n"
    message += f"📊 Asset: *{coin}*\n"
    message += f"💰 Size: {size}\n"
    message += f"💲 Price: ${float(price):,.4f}\n"
    message += f"💵 Value: {usd_formatted}\n"
    message += f"💸 Fee: ${float(fee):,.4f}\n"
    message += f"⏰ Time: {trade_time}\n"
    message += "━━━━━━━━━━━━━━\n"
    message += f"🔗 [View on Hyperliquid](https://app.hyperliquid.xyz/explorer/address/{address})"
    return message


async def initialize_wallet_time(session, address):
    """
    When a new wallet is added set its last trade time
    to the most recent trade so we dont send old notifications
    """
    import time

    # Get current time as default
    current_time = int(time.time() * 1000)

    try:
        trades = await fetch_trades(session, address)
        if trades:
            # Get the most recent trade time
            latest = max(t.get("time", 0) for t in trades)
            return latest
        else:
            return current_time
    except Exception:
        return current_time


async def check_wallet(session, address, chat_ids, labels, bot):
    still_monitored = await is_wallet_monitored(address)
    if not still_monitored:
        logger.info(f"Skipping {address} - no longer monitored")
        return

    trades = await fetch_trades(session, address)
    if not trades:
        return

    last_time = await get_last_trade_time(address)

    # ✅ ONLY GET TRADES THAT HAPPENED AFTER WE STARTED MONITORING
    new_trades = [t for t in trades if t.get("time", 0) > last_time]

    if not new_trades:
        return

    new_trades.sort(key=lambda x: x.get("time", 0))
    latest_time = max(t.get("time", 0) for t in new_trades)
    await update_last_trade_time(address, latest_time)

    label = labels.get(address)

    for trade in new_trades:
        still_monitored = await is_wallet_monitored(address)
        if not still_monitored:
            logger.info(f"Stopped notifications for {address} - removed")
            return

        message = format_trade_message(trade, address, label)

        for chat_id in chat_ids:
            user_wallets = await get_wallets_by_chat(chat_id)
            user_addresses = [w[0] for w in user_wallets]

            if address not in user_addresses:
                logger.info(f"Skipping {chat_id} - they removed {address}")
                continue

            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                logger.info(f"Sent trade alert to {chat_id}")
            except Exception as e:
                logger.error(f"Error sending to {chat_id}: {e}")


async def monitor_loop(bot):
    logger.info("Monitor loop started!")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                rows = await get_all_wallets()

                if rows:
                    wallet_map = {}
                    label_map = {}

                    for address, chat_id, label in rows:
                        if address not in wallet_map:
                            wallet_map[address] = []
                        wallet_map[address].append(chat_id)
                        if label:
                            label_map[address] = label

                    tasks = []
                    for addr, cids in wallet_map.items():
                        task = check_wallet(
                            session, addr, cids, label_map, bot
                        )
                        tasks.append(task)

                    await asyncio.gather(*tasks)

                else:
                    logger.info("No wallets being monitored")

            except Exception as e:
                logger.error(f"Monitor loop error: {e}")

            await asyncio.sleep(CHECK_INTERVAL)
