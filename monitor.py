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
    """
    Determine trade action using signed position sizes.
    pos_before and pos_after:
      positive = LONG
      negative = SHORT
      0 = flat/closed
    Returns: action, direction, dir_emoji, full_label
    """

    # ── Flip: crossed zero ──────────────────────────
    if (pos_before > 0 and pos_after < 0) or \
       (pos_before < 0 and pos_after > 0):
        direction = "LONG" if pos_after > 0 else "SHORT"
        dir_emoji = "🟢" if pos_after > 0 else "🔴"
        action = "Flip"
        full_label = f"{'🟢 LONG' if pos_after > 0 else '🔴 SHORT'} FLIP"
        return action, direction, dir_emoji, full_label

    # ── Open: was flat, now has position ────────────
    if pos_before == 0 and pos_after != 0:
        direction = "LONG" if pos_after > 0 else "SHORT"
        dir_emoji = "🟢" if pos_after > 0 else "🔴"
        action = "Open"
        full_label = f"{'🟢 LONG' if pos_after > 0 else '🔴 SHORT'} OPEN"
        return action, direction, dir_emoji, full_label

    # ── Close: had position, now flat ───────────────
    if pos_after == 0 and pos_before != 0:
        direction = "LONG" if pos_before > 0 else "SHORT"
        dir_emoji = "🟢" if pos_before > 0 else "🔴"
        action = "Close"
        full_label = f"{'🟢 LONG' if pos_before > 0 else '🔴 SHORT'} CLOSE"
        return action, direction, dir_emoji, full_label

    # ── Both same side ───────────────────────────────
    if pos_before > 0 and pos_after > 0:
        direction = "LONG"
        dir_emoji = "🟢"
        if pos_after > pos_before:
            action = "Increase"
            full_label = "🟢 LONG INCREASE"
        else:
            action = "Reduce"
            full_label = "🟢 LONG REDUCE"
        return action, direction, dir_emoji, full_label

    if pos_before < 0 and pos_after < 0:
        direction = "SHORT"
        dir_emoji = "🔴"
        if abs(pos_after) > abs(pos_before):
            action = "Increase"
            full_label = "🔴 SHORT INCREASE"
        else:
            action = "Reduce"
            full_label = "🔴 SHORT REDUCE"
        return action, direction, dir_emoji, full_label

    # ── Fallback ─────────────────────────────────────
    direction = "LONG" if side == "B" else "SHORT"
    dir_emoji = "🟢" if side == "B" else "🔴"
    action = "Open"
    full_label = f"{'🟢 LONG' if side == 'B' else '🔴 SHORT'} OPEN"
    return action, direction, dir_emoji, full_label


def get_action_short(side, pos_before, pos_after):
    """
    Returns short label for summary tree view.
    Uses signed pos values.
    """
    # Flip
    if (pos_before > 0 and pos_after < 0) or \
       (pos_before < 0 and pos_after > 0):
        direction = "LONG" if pos_after > 0 else "SHORT"
        return f"FLIP→{direction}"

    # Open
    if pos_before == 0 and pos_after != 0:
        direction = "LONG" if pos_after > 0 else "SHORT"
        return f"{direction} OPEN "

    # Close
    if pos_after == 0 and pos_before != 0:
        direction = "LONG" if pos_before > 0 else "SHORT"
        return f"{direction} CLOSE"

    # Long side
    if pos_before > 0 and pos_after > 0:
        if pos_after > pos_before:
            return "LONG ADD  "
        else:
            return "LONG CUT  "

    # Short side
    if pos_before < 0 and pos_after < 0:
        if abs(pos_after) > abs(pos_before):
            return "SHORT ADD "
        else:
            return "SHORT CUT "

    # Fallback
    direction = "LONG" if side == "B" else "SHORT"
    return f"{direction} OPEN "


def get_summary_header(trades_with_context):
    """
    Analyze all trades to build a smart summary header.
    trades_with_context: list of (trade, pos_before, pos_after)
    Returns a descriptive header string.
    """
    actions = set()
    directions = set()

    for trade, pos_before, pos_after in trades_with_context:
        side = trade.get("side", "B")

        # Flip
        if (pos_before > 0 and pos_after < 0) or \
           (pos_before < 0 and pos_after > 0):
            actions.add("FLIP")
            directions.add("LONG" if pos_after > 0 else "SHORT")
            continue

        # Open
        if pos_before == 0 and pos_after != 0:
            actions.add("OPEN")
            directions.add("LONG" if pos_after > 0 else "SHORT")
            continue

        # Close
        if pos_after == 0 and pos_before != 0:
            actions.add("CLOSE")
            directions.add("LONG" if pos_before > 0 else "SHORT")
            continue

        # Long side
        if pos_before > 0 and pos_after > 0:
            directions.add("LONG")
            if pos_after > pos_before:
                actions.add("INCREASE")
            else:
                actions.add("REDUCE")
            continue

        # Short side
        if pos_before < 0 and pos_after < 0:
            directions.add("SHORT")
            if abs(pos_after) > abs(pos_before):
                actions.add("INCREASE")
            else:
                actions.add("REDUCE")
            continue

        # Fallback
        directions.add("LONG" if side == "B" else "SHORT")
        actions.add("OPEN")

    # Build direction string
    if "LONG" in directions and "SHORT" in directions:
        dir_str = "🟢 LONG & 🔴 SHORT"
    elif "LONG" in directions:
        dir_str = "🟢 LONG"
    else:
        dir_str = "🔴 SHORT"

    # Build action string
    action_priority = ["FLIP", "OPEN", "CLOSE", "INCREASE", "REDUCE"]
    sorted_actions = [a for a in action_priority if a in actions]
    action_str = " + ".join(sorted_actions) if sorted_actions else "TRADE"

    return f"{dir_str} {action_str}"


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

    pos_before = float(position_before_size)

    action, direction, dir_emoji, full_label = determine_action(
        side, pos_before, pos_sz
    )

    wallet_name = (
        label.upper() if label
        else f"{address[:8]}...{address[-6:]}"
    )
    trade_time = to_nepal_time(timestamp)

    msg = f"👁️ *{wallet_name}*\n"
    msg += f"👛 `{address}`\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"🔔 *{full_label}*\n"
    msg += f"✳️ *{coin}/USDC*\n"
    msg += f"• Quantity: `{abs(pos_before):.4f}` → `{abs(pos_sz):.4f}`\n"
    msg += f"• Order Value: `${order_value:,.2f}`\n"
    msg += f"• Avg. Price: `${px:.4f}`\n"
    msg += f"\n📊 *POSITION*\n"

    if pos_sz != 0:
        msg += f"• Total Value: `${total_value:,.2f}`\n"
        msg += f"• Avg. Entry: `${entry:.4f}`\n"
        msg += f"• Leverage: `{lev}x`\n"
        if liq > 0:
            msg += f"• Liq Price: `${liq:.4f}`\n"
        msg += (
            f"• Unrealized PnL: {pnl_emoji} `${upnl:,.2f}`"
            f" (`{pnl_sign}{upnl_pct:.2f}%`)\n"
        )
    else:
        msg += f"• Position: *Closed* ✅\n"
        msg += f"• Realized PnL: {pnl_emoji} `${upnl:,.2f}`\n"

    msg += f"\n📅 `{trade_time}` (NPT)\n"
    msg += f"🔗 [View on Hyperdash](https://hyperdash.com/address/{address})"

    return msg


def format_summary_message(
    trades,
    address,
    label,
    asset_positions,
    prev_pos_map
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

    # ── Build trades with context for smart header ──
    trades_with_context = []
    temp_positions = dict(prev_pos_map)

    for trade in sorted(trades, key=lambda x: x.get("time", 0)):
        coin = trade.get("coin", "")
        side = trade.get("side", "B")
        sz = float(trade.get("sz", 0))
        pb = temp_positions.get(coin, 0)

        if side == "B":
            pa = pb + sz
        else:
            pa = pb - sz

        trades_with_context.append((trade, pb, pa))
        temp_positions[coin] = pa

    # ── Smart header ────────────────────────────────
    smart_header = get_summary_header(trades_with_context)

    msg = f"👁️ *{wallet_name}*\n"
    msg += f"👛 `{address}`\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"⚡ *HIGH ACTIVITY*\n"
    msg += f"📌 *{smart_header}*\n"
    msg += f"🔢 *{len(trades)} Trades Detected*\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

    for coin, ctrades in coin_trades.items():
        # Determine coin-level direction
        running_pos = prev_pos_map.get(coin, 0)

        # Calculate what happens across all trades for this coin
        sim_pos = running_pos
        for t in ctrades:
            s = t.get("side", "B")
            sz = float(t.get("sz", 0))
            if s == "B":
                sim_pos += sz
            else:
                sim_pos -= sz

        final_pos = sim_pos

        # Coin header
        if running_pos == 0 and final_pos > 0:
            coin_header = f"🟢 *{coin}/USDC* — LONG OPEN"
        elif running_pos == 0 and final_pos < 0:
            coin_header = f"🔴 *{coin}/USDC* — SHORT OPEN"
        elif final_pos == 0 and running_pos > 0:
            coin_header = f"🟢 *{coin}/USDC* — LONG CLOSE"
        elif final_pos == 0 and running_pos < 0:
            coin_header = f"🔴 *{coin}/USDC* — SHORT CLOSE"
        elif running_pos > 0 and final_pos > 0:
            if final_pos > running_pos:
                coin_header = f"🟢 *{coin}/USDC* — LONG INCREASE"
            else:
                coin_header = f"🟢 *{coin}/USDC* — LONG REDUCE"
        elif running_pos < 0 and final_pos < 0:
            if abs(final_pos) > abs(running_pos):
                coin_header = f"🔴 *{coin}/USDC* — SHORT INCREASE"
            else:
                coin_header = f"🔴 *{coin}/USDC* — SHORT REDUCE"
        elif (running_pos > 0 and final_pos < 0):
            coin_header = f"🔄 *{coin}/USDC* — FLIP → SHORT"
        elif (running_pos < 0 and final_pos > 0):
            coin_header = f"🔄 *{coin}/USDC* — FLIP → LONG"
        else:
            coin_header = f"⚡ *{coin}/USDC*"

        msg += f"{coin_header}"
        msg += f" — {len(ctrades)} Trade{'s' if len(ctrades) > 1 else ''}\n"

        # Trade tree
        r_pos = running_pos
        for i, trade in enumerate(ctrades):
            side = trade.get("side", "B")
            price = float(trade.get("px", 0))
            size = float(trade.get("sz", 0))
            value = size * price
            trade_time = to_nepal_time(trade.get("time", 0))

            if side == "B":
                new_pos = r_pos + size
            else:
                new_pos = r_pos - size

            action = get_action_short(side, r_pos, new_pos)
            r_pos = new_pos

            is_last = (i == len(ctrades) - 1)
            tree = "└" if is_last else "├"

            msg += (
                f"{tree} `{action}` "
                f"`{size:.4f}` @ `${price:,.4f}`"
                f" → `${value:,.2f}`\n"
            )
            msg += f"  🕐 `{trade_time}`\n"

        # Current position state
        pos = get_position_details(coin, asset_positions)
        if pos and float(pos.get("szi", 0) or 0) != 0:
            pos_size = float(pos.get("szi", 0) or 0)
            entry = float(pos.get("entryPx", 0) or 0)
            upnl = float(pos.get("unrealizedPnl", 0) or 0)
            liq = float(pos.get("liquidationPx", 0) or 0)
            lev = calculate_leverage(pos)
            pos_value = abs(pos_size) * entry
            direction = "LONG" if pos_size > 0 else "SHORT"
            pnl_str = (
                f"🟢 +`${upnl:,.2f}`"
                if upnl >= 0
                else f"🔴 -`${abs(upnl):,.2f}`"
            )
            msg += f"\n  📊 *Now {direction}*\n"
            msg += f"  • Size: `{abs(pos_size):.4f}`\n"
            msg += f"  • Value: `${pos_value:,.2f}`\n"
            msg += f"  • Entry: `${entry:,.4f}`\n"
            msg += f"  • Lev: `{lev}x`\n"
            if liq > 0:
                msg += f"  • Liq: `${liq:,.4f}`\n"
            msg += f"  • PnL: {pnl_str}\n"
        else:
            msg += f"\n  📊 *Position: Closed* ✅\n"

        msg += "\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🕐 `{start_time}` →\n"
    msg += f"   `{end_time}` (NPT)\n\n"
    msg += f"🔗 [View on Hyperdash](https://hyperdash.com/address/{address})"

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

    # Store SIGNED sizes (positive=LONG, negative=SHORT)
    current_positions = {}
    for p in asset_positions:
        pos = p.get("position", {})
        coin = pos.get("coin", "")
        size = float(pos.get("szi", 0) or 0)
        if coin:
            current_positions[coin] = size

    prev_pos_map = previous_positions.get(address, {})

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
        # Simulate signed position step by step
        running_positions = dict(prev_pos_map)

        for trade in new_trades:
            coin = trade.get("coin", "")
            side = trade.get("side", "B")
            sz = float(trade.get("sz", 0))

            # Signed position BEFORE this trade
            pos_before_size = running_positions.get(coin, 0)

            # Simulate signed position AFTER this trade
            if side == "B":
                simulated_after = pos_before_size + sz
            else:
                simulated_after = pos_before_size - sz

            # Update running for next trade
            running_positions[coin] = simulated_after

            # Get live position details for display
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
            f" — sending summary"
        )
        summary = format_summary_message(
            new_trades,
            address,
            label,
            asset_positions,
            prev_pos_map
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
                    prev_pos_map
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
