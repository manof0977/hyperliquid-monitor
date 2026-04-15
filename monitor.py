import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from config import HYPERLIQUID_API, HEADERS, CHECK_INTERVAL
from database import (
    get_all_wallets,
    get_last_trade_time,
    update_last_trade_time,
    is_wallet_monitored,
    get_wallets_by_chat
)

logger = logging.getLogger(__name__)

NEPAL_OFFSET = timedelta(hours=5, minutes=45)
previous_positions = {}
message_queue = asyncio.Queue()
MESSAGE_DELAY = 2.0
MAX_INDIVIDUAL_TRADES = 3


def to_nepal_time(utc_timestamp_ms):
    try:
        utc_dt = datetime.utcfromtimestamp(utc_timestamp_ms / 1000)
        nepal_dt = utc_dt + NEPAL_OFFSET
        return nepal_dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return "Unknown Time"


async def fetch_info(session, payload):
    try:
        async with session.post(
            HYPERLIQUID_API,
            json=payload,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            return None
    except Exception as e:
        logger.error(f"API Error: {e}")
        return None


async def fetch_trades(session, address):
    result = await fetch_info(
        session,
        {"type": "userFills", "user": address}
    )
    return result if result else []


async def fetch_positions(session, address):
    result = await fetch_info(
        session,
        {"type": "clearinghouseState", "user": address}
    )
    return result if result else {}


async def fetch_open_orders(session, address):
    result = await fetch_info(
        session,
        {"type": "openOrders", "user": address}
    )
    return result if result else []


def calculate_leverage(pos):
    try:
        lev = pos.get("leverage", {})
        if isinstance(lev, dict):
            return int(lev.get("value", 1))
        return int(lev or 1)
    except Exception:
        return 1


def get_position_details(coin, asset_positions):
    for p in asset_positions:
        pos = p.get("position", {})
        if pos.get("coin") == coin:
            return pos
    return {}


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
    elif (
        (pos_before > 0 and pos_after < 0) or
        (pos_before < 0 and pos_after > 0)
    ):
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


def format_trade_message(
    trade,
    address,
    label,
    position_after=None,
    position_before_size=0
):
    coin = trade.get("coin", "Unknown")
    side = trade.get("side", "B")
    sz = float(trade.get("sz", 0))
    px = float(trade.get("px", 0))
    timestamp = trade.get("time", 0)
    order_value = sz * px

    pos = position_after or {}
    pos_sz = float(pos.get("szi", 0) or 0)
    entry = float(pos.get("entryPx", 0) or px)
    upnl = float(pos.get("unrealizedPnl", 0) or 0)
    liq = float(pos.get("liquidationPx", 0) or 0)
    lev = calculate_leverage(pos)
    total_value = abs(pos_sz) * entry

    if total_value > 0 and lev > 0:
        margin = total_value / lev
        upnl_pct = (upnl / margin) * 100 if margin > 0 else 0
    else:
        upnl_pct = 0.0

    pnl_sign = "+" if upnl >= 0 else ""
    pnl_emoji = "🟢" if upnl >= 0 else "🔴"

    action, direction, dir_emoji, action_str = determine_action(
        side, position_before_size, pos_sz
    )

    wallet_name = (
        label.upper() if label
        else f"{address[:8]}...{address[-6:]}"
    )
    trade_time = to_nepal_time(timestamp)

    msg = f"👁️ *{wallet_name}*\n"
    msg += f"👛 `{address}`\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"🔔 *ACTION — {dir_emoji} {direction}*\n"
    msg += f"✳️ {coin}/USDC ({action_str})\n"
    msg += f"• Quantity: {position_before_size:.4f} → {pos_sz:.4f}\n"
    msg += f"• Order Value: ${order_value:,.2f}\n"
    msg += f"• Avg. Price: ${px:.4f}\n"
    msg += f"\n📊 *POSITION*\n"

    if pos_sz != 0:
        msg += f"• Total Value: ${total_value:,.2f}\n"
        msg += f"• Avg. Entry: ${entry:.4f}\n"
        msg += f"• Leverage: {lev}x\n"
        if liq > 0:
            msg += f"• Liquidation Price: ${liq:.4f}\n"
        msg += (
            f"• Unrealized PnL: {pnl_emoji} ${upnl:,.2f}"
            f"({pnl_sign}{upnl_pct:.2f}%)\n"
        )
    else:
        msg += f"• Position Closed\n"
        msg += f"• Realized PnL: {pnl_emoji} ${upnl:,.2f}\n"

    msg += f"\n📅 {trade_time} (NPT)"
    return msg


def get_action_short(side, pos_before, pos_after):
    if pos_before == 0 and pos_after != 0:
        return "Open  "
    elif pos_after == 0 and pos_before != 0:
        return "Close "
    elif abs(pos_after) > abs(pos_before):
        return "Add   "
    elif abs(pos_after) < abs(pos_before):
        return "Cut   "
    else:
        return "Update"


def format_summary_message(
    trades,
    address,
    label,
    asset_positions,
    prev_positions
):
    wallet_name = (
        label.upper() if label
        else f"{address[:8]}...{address[-6:]}"
    )

    coin_trades = defaultdict(list)
    for trade in trades:
        coin_trades[trade.get("coin", "?")].append(trade)

    times = [t.get("time", 0) for t in trades]
    start_time = to_nepal_time(min(times))
    end_time = to_nepal_time(max(times))

    msg = f"👁️ *{wallet_name}*\n"
    msg += f"👛 `{address}`\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"⚡ *HIGH ACTIVITY — {len(trades)} Trades*\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

    for coin, ctrades in coin_trades.items():
        side_first = ctrades[0].get("side", "B")
        dir_emoji = "🟢" if side_first == "B" else "🔴"
        msg += (
            f"{dir_emoji} *{coin}/USDC*"
            f" — {len(ctrades)} Trade"
            f"{'s' if len(ctrades) > 1 else ''}\n"
        )

        running_pos = prev_positions.get(coin, 0)

        for i, trade in enumerate(ctrades):
            side = trade.get("side", "B")
            price = float(trade.get("px", 0))
            size = float(trade.get("sz", 0))
            value = size * price
            trade_time = to_nepal_time(trade.get("time", 0))

            if side == "B":
                new_pos = running_pos + size
            else:
                new_pos = running_pos - size

            action = get_action_short(side, running_pos, new_pos)
            running_pos = new_pos

            is_last = (i == len(ctrades) - 1)
            tree = "└" if is_last else "├"

            msg += (
                f"{tree} `{action}` "
                f"`{size:.4f}` @ `${price:,.4f}`"
                f" → `${value:,.2f}`\n"
            )
            msg += f"   🕐 `{trade_time}`\n"

        pos = get_position_details(coin, asset_positions)
        if pos:
            pos_size = float(pos.get("szi", 0) or 0)
            if pos_size != 0:
                entry = float(pos.get("entryPx", 0) or 0)
                upnl = float(pos.get("unrealizedPnl", 0) or 0)
                liq = float(pos.get("liquidationPx", 0) or 0)
                lev = calculate_leverage(pos)
                pos_value = abs(pos_size) * entry
                direction = "LONG" if pos_size > 0 else "SHORT"
                pnl_str = (
                    f"🟢 +${upnl:,.2f}"
                    if upnl >= 0
                    else f"🔴 -${abs(upnl):,.2f}"
                )
                msg += f"\n   📊 *Now {direction}*\n"
                msg += f"   • Size: `{abs(pos_size):.4f}`\n"
                msg += f"   • Value: `${pos_value:,.2f}`\n"
                msg += f"   • Entry: `${entry:,.4f}`\n"
                msg += f"   • Lev: `{lev}x`\n"
                if liq > 0:
                    msg += f"   • Liq: `${liq:,.4f}`\n"
                msg += f"   • PnL: {pnl_str}\n"
            else:
                msg += f"\n   📊 Position: *Closed*\n"
        else:
            msg += f"\n   📊 Position: *Closed*\n"

        msg += "\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🕐 `{start_time}` →\n"
    msg += f"   `{end_time}` (NPT)"
    return msg


async def message_queue_worker(bot):
    logger.info("✅ Message queue worker started!")
    while True:
        try:
            item = await message_queue.get()
            chat_id = item["chat_id"]
            message = item["message"]
            retry_count = item.get("retry", 0)

            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                logger.info(
                    f"✅ Delivered to {chat_id} "
                    f"(Queue: {message_queue.qsize()} left)"
                )
            except Exception as e:
                error_str = str(e)
                if "Flood control" in error_str or "429" in error_str:
                    try:
                        wait = int(
                            error_str.split("Retry in ")[1].split(" ")[0]
                        )
                        wait = min(wait, 60)
                    except Exception:
                        wait = 15
                    logger.warning(f"⚠️ Flood control — waiting {wait}s")
                    await asyncio.sleep(wait)
                    if retry_count < 5:
                        await message_queue.put({
                            "chat_id": chat_id,
                            "message": message,
                            "retry": retry_count + 1
                        })
                else:
                    logger.error(f"Send error: {e}")

            message_queue.task_done()
            await asyncio.sleep(MESSAGE_DELAY)

        except Exception as e:
            logger.error(f"Queue worker error: {e}")
            await asyncio.sleep(1)


async def queue_message(chat_id, message):
    await message_queue.put({
        "chat_id": chat_id,
        "message": message,
        "retry": 0
    })


async def check_wallet(session, address, chat_ids, labels, bot):
    global previous_positions

    still_monitored = await is_wallet_monitored(address)
    if not still_monitored:
        return

    position_data = await fetch_positions(session, address)
    asset_positions = position_data.get("assetPositions", [])

    current_positions = {}
    for p in asset_positions:
        pos = p.get("position", {})
        coin = pos.get("coin", "")
        size = float(pos.get("szi", 0) or 0)
        if coin:
            current_positions[coin] = size

    prev_positions = previous_positions.get(address, {})

    trades = await fetch_trades(session, address)
    if not trades:
        previous_positions[address] = current_positions
        return

    last_time = await get_last_trade_time(address)
    new_trades = [
        t for t in trades
        if t.get("time", 0) > last_time
    ]

    if not new_trades:
        previous_positions[address] = current_positions
        return

    new_trades.sort(key=lambda x: x.get("time", 0))
    latest_time = max(t.get("time", 0) for t in new_trades)
    await update_last_trade_time(address, latest_time)

    label = labels.get(address)

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
        f"📊 {address[:8]} — {len(new_trades)} new trade(s)"
    )

    if len(new_trades) <= MAX_INDIVIDUAL_TRADES:
        for trade in new_trades:
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
            for chat_id in valid_chats:
                await queue_message(chat_id, message)
    else:
        logger.info(
            f"⚡ {address[:8]} has {len(new_trades)} trades"
            f" — sending full summary"
        )
        summary = format_summary_message(
            new_trades,
            address,
            label,
            asset_positions,
            prev_positions
        )
        if len(summary) > 4000:
            coin_trades = defaultdict(list)
            for trade in new_trades:
                coin_trades[trade.get("coin", "?")].append(trade)
            for coin, ctrades in coin_trades.items():
                coin_summary = format_summary_message(
                    ctrades,
                    address,
                    label,
                    asset_positions,
                    prev_positions
                )
                for chat_id in valid_chats:
                    await queue_message(chat_id, coin_summary)
        else:
            for chat_id in valid_chats:
                await queue_message(chat_id, summary)

    previous_positions[address] = current_positions


async def monitor_loop(bot):
    logger.info("Monitor loop started!")
    asyncio.create_task(message_queue_worker(bot))

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                rows = await get_all_wallets()
                if rows:
                    wallet_map = defaultdict(list)
                    label_map = {}
                    for address, chat_id, label in rows:
                        wallet_map[address].append(chat_id)
                        if label:
                            label_map[address] = label

                    tasks = [
                        check_wallet(
                            session, addr, cids, label_map, bot
                        )
                        for addr, cids in wallet_map.items()
                    ]
                    await asyncio.gather(*tasks)
                else:
                    logger.info("No wallets being monitored")

            except Exception as e:
                logger.error(f"Monitor loop error: {e}")

            await asyncio.sleep(CHECK_INTERVAL)
