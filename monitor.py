import aiohttp
import asyncio
import logging
from datetime import datetime
from config import HYPERLIQUID_API, HEADERS, CHECK_INTERVAL
from database import (
    get_all_wallets,
    get_last_trade_time,
    update_last_trade_time,
    is_wallet_monitored,
    get_wallets_by_chat
)

logger = logging.getLogger(__name__)

previous_positions = {}


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
        pct = ((abs(pos_after) - abs(pos_before)) / abs(pos_before)) * 100
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
        side,
        position_before_size,
        pos_size_after
    )

    wallet_name = label.upper() if label else f"{address[:8]}...{address[-6:]}"

    trade_time = datetime.utcfromtimestamp(
        timestamp / 1000
    ).strftime('%Y-%m-%d %H:%M:%S')

    quantity_change = format_quantity_change(
        position_before_size,
        pos_size_after
    )

    msg = ""

    msg += f"👁️ *{wallet_name}*\n"
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
        msg += f"• Unrealized PnL: ${upnl:.2f}({pnl_sign}{upnl_pct:.2f}%)\n"
        msg += f"• Leverage: {leverage}x\n"
    else:
        msg += f"• Position Closed\n"
        msg += f"• Realized PnL: ${upnl:.2f}\n"

    msg += f"\n📅 {trade_time} (UTC+0)"

    return msg


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
    new_trades = [t for t in trades if t.get("time", 0) > last_time]

    if not new_trades:
        previous_positions[address] = current_positions
        return

    new_trades.sort(key=lambda x: x.get("time", 0))
    latest_time = max(t.get("time", 0) for t in new_trades)
    await update_last_trade_time(address, latest_time)

    label = labels.get(address)

    for trade in new_trades:
        still_monitored = await is_wallet_monitored(address)
        if not still_monitored:
            return

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

        for chat_id in chat_ids:
            user_wallets = await get_wallets_by_chat(chat_id)
            user_addresses = [w[0] for w in user_wallets]

            if address not in user_addresses:
                continue

            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                logger.info(f"✅ Sent {coin} alert to {chat_id}")
            except Exception as e:
                logger.error(f"Error sending to {chat_id}: {e}")

    previous_positions[address] = current_positions


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
