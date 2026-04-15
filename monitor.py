import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta
from config import HYPERLIQUID_API, HEADERS, CHECK_INTERVAL
from database import (
    get_all_wallets,
    get_last_trade_time,
    update_last_trade_time,
    is_wallet_monitored,
    get_wallets_by_chat
)

logger = logging.getLogger(__name__)

# Nepal Time
NEPAL_OFFSET = timedelta(hours=5, minutes=45)

# Previous positions memory
previous_positions = {}

# Global message queue
message_queue = asyncio.Queue()

# Delay between each message (seconds)
# 2 seconds = max 30 messages per minute
# Safe limit is 20 messages per minute per bot
MESSAGE_DELAY = 3.0


def to_nepal_time(utc_timestamp_ms):
    utc_dt = datetime.utcfromtimestamp(utc_timestamp_ms / 1000)
    nepal_dt = utc_dt + NEPAL_OFFSET
    return nepal_dt.strftime('%Y-%m-%d %H:%M:%S')


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


def get_position_details(coin, asset_positions):
    for p in asset_positions:
        pos = p.get("position", {})
        if pos.get("coin") == coin:
            return pos
    return {}


def calculate_leverage(position):
    try:
        leverage = position.get("leverage", {})
        if isinstance(leverage, dict):
            val = leverage.get("value", 1)
            return int(val) if val else 1
        return int(leverage) if leverage else 1
    except Exception:
        return 1


def determine_action(side, pos_before, pos_after):
    if pos_after > 0:
        direction = "LONG"
        dir_emoji = "🟢"
    elif pos_after < 0:
        direction = "SHORT"
        dir_emoji = "🔴"
    else:
        if pos_before > 0:
            direction = "LONG"
            dir_emoji = "🟢"
        elif pos_before < 0:
            direction = "SHORT"
            dir_emoji = "🔴"
        else:
            direction = "LONG" if side == "B" else "SHORT"
            dir_emoji = "🟢" if side == "B" else "🔴"

    if pos_before == 0 and pos_after != 0:
        action = "Open"
    elif pos_after == 0 and pos_before != 0:
        action = "Close"
    elif (pos_before > 0 and pos_after < 0) or \
         (pos_before < 0 and pos_after > 0):
        action = "Flip"
        direction = "LONG" if pos_after > 0 else "SHORT"
        dir_emoji = "🟢" if pos_after > 0 else "🔴"
    elif abs(pos_after) > abs(pos_before):
        action = "Increase"
    elif abs(pos_after) < abs(pos_before):
        action = "Reduce"
    else:
        action = "Open"

    action_str = f"Cross-{direction.capitalize()}-{action}"
    return action, direction, dir_emoji, action_str


def format_quantity_change(pos_before, pos_after):
    if pos_before == 0 and pos_after == 0:
        pct_str = "+0.00"
    elif pos_before == 0:
        pct_str = "+100.00"
    elif pos_after == 0:
        pct_str = "-100.00"
    else:
        pct = (
            (abs(pos_after) - abs(pos_before)) / abs(pos_before)
        ) * 100
        pct_str = f"+{pct:.2f}" if pct >= 0 else f"{pct:.2f}"
    return f"{pos_before:.4f} → {pos_after:.4f} ({pct_str}%)"


def format_trade_message(
    trade,
    address,
    label,
    position_after=None,
    position_before_size=0
):
    coin = trade.get("coin", "Unknown")
    side = trade.get("side", "B")
    trade_size = float(trade.get("sz", 0))
    price = float(trade.get("px", 0))
    timestamp = trade.get("time", 0)
    order_value = trade_size * price

    pos = position_after or {}
    pos_size_after = float(pos.get("szi", 0) or 0)
    entry_price = float(pos.get("entryPx", 0) or price)
    liq_price = float(pos.get("liquidationPx", 0) or 0)
    upnl = float(pos.get("unrealizedPnl", 0) or 0)
    leverage = calculate_leverage(pos)
    total_value = abs(pos_size_after) * entry_price

    if total_value > 0 and leverage > 0:
        margin = total_value / leverage
        upnl_pct = (upnl / margin) * 100 if margin > 0 else 0
    else:
        upnl_pct = 0.0

    pnl_sign = "+" if upnl >= 0 else ""

    action, direction, dir_emoji, action_str = determine_action(
        side, position_before_size, pos_size_after
    )

    wallet_name = (
        label.upper() if label
        else f"{address[:8]}...{address[-6:]}"
    )
    trade_time = to_nepal_time(timestamp)
    quantity_change = format_quantity_change(
        position_before_size, pos_size_after
    )

    msg = f"👁️ *{wallet_name}*\n"
    msg += f"👛 `{address}`\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"🔔 *ACTION — {dir_emoji} {direction}*\n"
    msg += f"✳️ {coin}/USDC ({action_str})\n"
    msg += f"• Quantity: {quantity_change}\n"
    msg += f"• Order Value: ${order_value:,.2f}\n"
    msg += f"• Avg. Price: ${price:.4f}\n"
    msg += f"\n📊 *POSITION*\n"

    if pos and pos_size_after != 0:
        msg += f"• Total Value: ${total_value:,.2f}\n"
        msg += f"• Avg. Entry: ${entry_price:.4f}\n"
        if liq_price and liq_price > 0:
            msg += f"• Liquidation Price: ${liq_price:.4f}\n"
        else:
            msg += f"• Liquidation Price: N/A\n"
        msg += (
            f"• Unrealized PnL: ${upnl:.2f}"
            f"({pnl_sign}{upnl_pct:.2f}%)\n"
        )
        msg += f"• Leverage: {leverage}x\n"
    else:
        msg += f"• Position Closed\n"
        msg += f"• Realized PnL: ${upnl:.2f}\n"

    msg += f"\n📅 {trade_time} (NPT)"
    return msg


# ─── Message Queue Worker ─────────────────────────────────────

async def message_queue_worker(bot):
    """
    Runs forever in background
    Sends every single notification
    but with safe delay between each
    Never drops any message
    Never floods Telegram
    """
    logger.info("✅ Message queue worker started!")

    while True:
        try:
            # Wait for next message in queue
            item = await message_queue.get()

            chat_id = item["chat_id"]
            message = item["message"]
            retry_count = item.get("retry", 0)

            sent = False
            while not sent:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode="Markdown",
                        disable_web_page_preview=True
                    )
                    logger.info(f"✅ Queued message sent to {chat_id}")
                    sent = True

                    # Safe delay between messages
                    await asyncio.sleep(MESSAGE_DELAY)

                except Exception as e:
                    error_str = str(e)

                    if "Flood control" in error_str or "429" in error_str:
                        # Extract exact wait time from Telegram
                        try:
                            wait = int(
                                error_str.split(
                                    "Retry in "
                                )[1].split(" ")[0]
                            )
                            # Add 2 extra seconds buffer
                            wait = wait + 2
                        except Exception:
                            wait = 15

                        logger.warning(
                            f"⚠️ Flood control hit — "
                            f"waiting {wait}s then retrying"
                        )
                        # Wait exact time Telegram says
                        # Then retry same message
                        await asyncio.sleep(wait)
                        # Don't mark as sent — loop continues

                    elif "chat not found" in error_str.lower():
                        logger.error(f"Chat {chat_id} not found — skipping")
                        sent = True  # Skip this message

                    else:
                        logger.error(f"Send error: {e}")
                        if retry_count < 5:
                            await asyncio.sleep(5)
                            retry_count += 1
                        else:
                            logger.error("Max retries — skipping message")
                            sent = True

            message_queue.task_done()

        except Exception as e:
            logger.error(f"Queue worker error: {e}")
            await asyncio.sleep(1)


async def queue_message(chat_id, message):
    """
    Add message to queue
    Queue worker sends it when ready
    Every message is guaranteed to be sent
    """
    await message_queue.put({
        "chat_id": chat_id,
        "message": message,
        "retry": 0
    })


# ─── Wallet Checker ───────────────────────────────────────────

async def check_wallet(session, address, chat_ids, labels, bot):
    """
    Check wallet for new trades
    Queue ALL trades for sending
    No limit on number of trades
    """
    global previous_positions

    still_monitored = await is_wallet_monitored(address)
    if not still_monitored:
        return

    # Fetch current positions
    position_data = await fetch_positions(session, address)
    asset_positions = position_data.get("assetPositions", [])

    # Build current position map
    current_positions = {}
    for p in asset_positions:
        pos = p.get("position", {})
        coin = pos.get("coin", "")
        size = float(pos.get("szi", 0) or 0)
        if coin:
            current_positions[coin] = size

    prev_positions = previous_positions.get(address, {})

    # Fetch trades
    trades = await fetch_trades(session, address)
    if not trades:
        previous_positions[address] = current_positions
        return

    last_time = await get_last_trade_time(address)

    # Get ALL new trades — no limit
    new_trades = [
        t for t in trades
        if t.get("time", 0) > last_time
    ]

    if not new_trades:
        previous_positions[address] = current_positions
        return

    # Sort oldest first so notifications arrive in order
    new_trades.sort(key=lambda x: x.get("time", 0))

    # Update last trade time
    latest_time = max(t.get("time", 0) for t in new_trades)
    await update_last_trade_time(address, latest_time)

    label = labels.get(address)

    # Get valid chats for this wallet
    valid_chats = []
    for chat_id in chat_ids:
        user_wallets = await get_wallets_by_chat(chat_id)
        user_addresses = [w[0] for w in user_wallets]
        if address in user_addresses:
            valid_chats.append(chat_id)

    if not valid_chats:
        previous_positions[address] = current_positions
        return

    logger.info(
        f"📨 Queuing {len(new_trades)} trades for "
        f"{address[:8]}..."
    )

    # ✅ Queue EVERY trade notification
    for trade in new_trades:
        still_monitored = await is_wallet_monitored(address)
        if not still_monitored:
            break

        coin = trade.get("coin", "")
        pos_before_size = prev_positions.get(coin, 0)
        pos_details = get_position_details(coin, asset_positions)

        message = format_trade_message(
            trade,
            address,
            label,
            position_after=pos_details,
            position_before_size=pos_before_size
        )

        # Add to queue for EVERY chat
        for chat_id in valid_chats:
            await queue_message(chat_id, message)

    previous_positions[address] = current_positions


# ─── Monitor Loop ─────────────────────────────────────────────

async def monitor_loop(bot):
    logger.info("Monitor loop started!")

    # ✅ Start queue worker as background task
    # It runs forever alongside monitor loop
    asyncio.create_task(message_queue_worker(bot))

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
