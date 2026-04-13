import asyncio
import logging
import aiohttp
import time
from datetime import datetime
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler
)
from config import TELEGRAM_BOT_TOKEN, CHECK_INTERVAL
from database import (
    init_db,
    add_wallet,
    remove_wallet,
    get_wallets_by_chat,
    get_wallets_with_labels_by_chat
)
from monitor import (
    monitor_loop,
    fetch_positions,
    fetch_open_orders,
    fetch_trades
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👁️ *HYPERLIQUID WALLET MONITOR*\n\n"
        "Track wallets and get instant trade alerts!\n\n"
        "📋 *Commands:*\n"
        "`/add <address> [label]` — Add a wallet\n"
        "`/remove <address>` — Remove a wallet\n"
        "`/list` — Show your wallets\n"
        "`/positions <address>` — View positions\n"
        "`/orders <address>` — View open orders\n"
        "`/common` — Common coin activity\n"
        "`/help` — Show this message\n\n"
        f"🔄 Checking every {CHECK_INTERVAL} seconds"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Usage: `/add <wallet_address> [label]`\n"
            "Example: `/add 0xabc...123 MyTrader`",
            parse_mode="Markdown"
        )
        return

    address = args[0].lower()
    label = " ".join(args[1:]) if len(args) > 1 else None

    if not address.startswith("0x") or len(address) != 42:
        await update.message.reply_text(
            "❌ Invalid address! Must start with `0x` and be 42 characters.",
            parse_mode="Markdown"
        )
        return

    chat_id = update.effective_chat.id
    success = await add_wallet(chat_id, address, label)

    if success:
        label_text = f" (*{label}*)" if label else ""
        await update.message.reply_text(
            f"✅ Now monitoring:\n`{address}`{label_text}\n\n"
            f"Only NEW trades from this point will be notified!",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"⚠️ Already monitoring `{address}`",
            parse_mode="Markdown"
        )


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Usage: `/remove <wallet_address>`",
            parse_mode="Markdown"
        )
        return

    address = args[0].lower()
    chat_id = update.effective_chat.id
    success = await remove_wallet(chat_id, address)

    if success:
        await update.message.reply_text(
            f"🗑️ Removed `{address}`",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"❌ Wallet not found: `{address}`",
            parse_mode="Markdown"
        )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    wallets = await get_wallets_by_chat(chat_id)

    if not wallets:
        await update.message.reply_text(
            "📭 No wallets monitored yet.\n"
            "Use `/add <address>` to start!",
            parse_mode="Markdown"
        )
        return

    lines = ["👛 *YOUR MONITORED WALLETS*\n"]
    for i, (address, label) in enumerate(wallets, 1):
        label_str = f" — _{label}_" if label else ""
        short = f"{address[:8]}...{address[-6:]}"
        lines.append(f"{i}. `{short}`{label_str}")
        lines.append(
            f"   🔗 [Explorer]"
            f"(https://app.hyperliquid.xyz/explorer/address/{address})\n"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Usage: `/positions <address>`",
            parse_mode="Markdown"
        )
        return

    address = args[0].lower()
    await update.message.reply_text("⏳ Fetching positions...")

    async with aiohttp.ClientSession() as session:
        data = await fetch_positions(session, address)

    if not data:
        await update.message.reply_text("❌ Could not fetch data.")
        return

    asset_positions = data.get("assetPositions", [])
    account_value = data.get(
        "marginSummary", {}
    ).get("accountValue", "0")

    lines = [
        f"📊 *POSITIONS*\n"
        f"👛 `{address[:8]}...{address[-6:]}`\n"
        f"💼 Account Value: *${float(account_value):,.2f}*\n"
    ]

    active = [
        p for p in asset_positions
        if float(p.get("position", {}).get("szi", 0)) != 0
    ]

    if not active:
        lines.append("_No open positions_")
    else:
        for pos_data in active:
            pos = pos_data.get("position", {})
            coin = pos.get("coin", "?")
            size = float(pos.get("szi", 0))
            entry = float(pos.get("entryPx", 0) or 0)
            upnl = float(pos.get("unrealizedPnl", 0))
            direction = "🟢 LONG" if size > 0 else "🔴 SHORT"
            pnl_icon = "📈" if upnl >= 0 else "📉"
            pnl_sign = "+" if upnl >= 0 else ""
            lines.append(
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{direction} *{coin}*\n"
                f"• Size: {abs(size)}\n"
                f"• Entry: ${entry:,.4f}\n"
                f"• {pnl_icon} PnL: {pnl_sign}${upnl:,.2f}\n"
            )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown"
    )


async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Usage: `/orders <address>`",
            parse_mode="Markdown"
        )
        return

    address = args[0].lower()

    async with aiohttp.ClientSession() as session:
        orders = await fetch_open_orders(session, address)

    if not orders:
        await update.message.reply_text(
            f"📋 No open orders for "
            f"`{address[:8]}...{address[-6:]}`",
            parse_mode="Markdown"
        )
        return

    lines = [
        f"📋 *OPEN ORDERS*\n"
        f"👛 `{address[:8]}...{address[-6:]}`\n"
    ]

    for order in orders[:10]:
        side = "🟢 BUY" if order.get("side") == "B" else "🔴 SELL"
        coin = order.get("coin", "?")
        size = order.get("sz", "0")
        price = order.get("limitPx", "0")
        lines.append(
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{side} *{coin}*\n"
            f"• Size: {size}\n"
            f"• Limit Price: ${float(price):,.4f}\n"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown"
    )


# ─── Common Trades ───────────────────────────────────────────


async def common_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton(
                "⏰ 1 Hour", callback_data="common_1"
            ),
            InlineKeyboardButton(
                "🕓 4 Hours", callback_data="common_4"
            ),
        ],
        [
            InlineKeyboardButton(
                "📅 1 Day", callback_data="common_24"
            ),
            InlineKeyboardButton(
                "📆 7 Days", callback_data="common_168"
            ),
        ],
        [
            InlineKeyboardButton(
                "🗓️ 30 Days", callback_data="common_720"
            )
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🔍 *COMMON COIN ACTIVITY*\n\n"
        "Select time range to analyze:\n"
        "Shows all coins being traded by\n"
        "multiple wallets — any direction!\n\n"
        "👇 Tap a button below",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


async def fetch_wallet_position(session, address, coin):
    """Fetch current position for a specific coin"""
    payload = {"type": "clearinghouseState", "user": address}
    try:
        async with session.post(
            "https://api.hyperliquid.xyz/info",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                positions = data.get("assetPositions", [])
                for p in positions:
                    pos = p.get("position", {})
                    if pos.get("coin") == coin:
                        return pos
            return None
    except Exception:
        return None


async def common_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    try:
        hours = int(query.data.split("_")[1])
    except Exception:
        hours = 1

    labels_map = {
        1:   "1 Hour",
        4:   "4 Hours",
        24:  "1 Day",
        168: "7 Days",
        720: "30 Days"
    }
    time_label = labels_map.get(hours, f"{hours} Hours")

    await query.edit_message_text(
        f"⏳ Scanning wallets for last *{time_label}*...",
        parse_mode="Markdown"
    )

    wallets = await get_wallets_with_labels_by_chat(chat_id)

    if not wallets:
        await query.edit_message_text(
            "📭 No wallets monitored yet.\n"
            "Use `/add <address>` to start!",
            parse_mode="Markdown"
        )
        return

    if len(wallets) < 2:
        await query.edit_message_text(
            "⚠️ You need at least *2 wallets* to compare!\n"
            "Add more with `/add <address>`",
            parse_mode="Markdown"
        )
        return

    total_wallets = len(wallets)
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - (hours * 60 * 60 * 1000)

    # {coin: [trader_details]}
    coin_activity = defaultdict(list)

    async with aiohttp.ClientSession() as session:
        for address, label in wallets:
            wallet_name = (
                label.upper() if label
                else f"{address[:6]}...{address[-4:]}"
            )

            try:
                trades = await fetch_trades(session, address)
                if not trades:
                    continue

                recent = [
                    t for t in trades
                    if t.get("time", 0) >= since_ms
                ]

                if not recent:
                    continue

                seen_coins = set()

                for trade in recent:
                    coin = trade.get("coin", "")
                    side = trade.get("side", "")
                    price = float(trade.get("px", 0))
                    size = float(trade.get("sz", 0))
                    order_value = price * size
                    trade_time = trade.get("time", 0)

                    if not coin:
                        continue

                    if coin not in seen_coins:
                        seen_coins.add(coin)

                        # Fetch current position for PnL
                        pos = await fetch_wallet_position(
                            session, address, coin
                        )

                        # Get position details
                        pos_size = 0
                        pos_value = 0
                        upnl = 0
                        entry_price = price
                        is_open = False

                        if pos:
                            pos_size = float(
                                pos.get("szi", 0) or 0
                            )
                            entry_price = float(
                                pos.get("entryPx", 0) or price
                            )
                            upnl = float(
                                pos.get("unrealizedPnl", 0) or 0
                            )
                            pos_value = abs(pos_size) * entry_price
                            is_open = pos_size != 0

                        coin_activity[coin].append({
                            "name": wallet_name,
                            "address": address,
                            "side": side,
                            "price": price,
                            "size": size,
                            "value": order_value,
                            "time": trade_time,
                            "pos_value": pos_value,
                            "upnl": upnl,
                            "is_open": is_open,
                            "entry_price": entry_price
                        })

            except Exception as e:
                logger.error(f"Error fetching {address}: {e}")
                continue

    # Filter coins with 2+ wallets
    common_coins = {
        coin: traders
        for coin, traders in coin_activity.items()
        if len(traders) >= 2
    }

    if not common_coins:
        await query.edit_message_text(
            f"📭 *No Common Coins Found*\n\n"
            f"No 2+ wallets traded the same coin\n"
            f"in last *{time_label}*\n\n"
            f"Try a longer time range!",
            parse_mode="Markdown"
        )
        return

    # Sort by most wallets
    sorted_coins = sorted(
        common_coins.items(),
        key=lambda x: len(x[1]),
        reverse=True
    )

    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

    msg = f"👁️ *COMMON COIN ACTIVITY*\n"
    msg += f"⏰ Last {time_label}\n"
    msg += f"👛 {total_wallets} Wallets Analyzed\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

    for coin, traders in sorted_coins:

        wallet_count = len(traders)
        longs = [t for t in traders if t["side"] == "B"]
        shorts = [t for t in traders if t["side"] == "A"]

        # Coin header
        msg += f"🪙 *{coin}/USDC*"
        msg += f" — {wallet_count}"
        msg += f" Wallet{'s' if wallet_count > 1 else ''}\n"

        # Long short split
        if longs and shorts:
            msg += (
                f"   🟢 {len(longs)} "
                f"Long{'s' if len(longs) > 1 else ''}"
                f" | 🔴 {len(shorts)} "
                f"Short{'s' if len(shorts) > 1 else ''}\n"
            )
        elif longs:
            msg += (
                f"   🟢 All Long"
                f"{'s' if len(longs) > 1 else ''}\n"
            )
        elif shorts:
            msg += (
                f"   🔴 All Short"
                f"{'s' if len(shorts) > 1 else ''}\n"
            )

        msg += f"━━━━━━━━━━━━━━━━━━━━\n"

        # Longs first then Shorts
        all_traders = []
        for t in longs:
            all_traders.append((t, "LONG", "🟢"))
        for t in shorts:
            all_traders.append((t, "SHORT", "🔴"))

        for trader, direction, emoji in all_traders:
            trade_time_str = datetime.utcfromtimestamp(
                trader["time"] / 1000
            ).strftime('%d/%m %H:%M')

            upnl = trader["upnl"]
            pos_value = trader["pos_value"]
            is_open = trader["is_open"]

            # PnL display with color emoji
            if upnl > 0:
                pnl_str = f"🟢 +${upnl:,.2f}"
            elif upnl < 0:
                pnl_str = f"🔴 -${abs(upnl):,.2f}"
            else:
                pnl_str = f"⚪ $0.00"

            # Position status
            status = "🔓 OPEN" if is_open else "🔒 CLOSED"

            msg += f"{emoji} *{direction}* — *{trader['name']}*\n"
            msg += (
                f"   📦 Size: `{trader['size']:.4f}`"
                f" @ `${trader['price']:,.4f}`\n"
            )
            msg += f"   💵 Trade Value: `${trader['value']:,.2f}`\n"

            if is_open:
                msg += (
                    f"   📊 Position: `${pos_value:,.2f}`"
                    f" | {pnl_str}\n"
                )
            else:
                msg += f"   📊 Position: {status}\n"

            msg += f"   🕐 `{trade_time_str}`\n\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📅 {now_str} (UTC+0)"

    if len(msg) > 4000:
        msg = msg[:3900] + "\n\n_...truncated. Use shorter range_"

    await query.edit_message_text(
        msg,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


async def main():
    await init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("remove", remove_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("positions", positions_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("common", common_command))
    app.add_handler(
        CallbackQueryHandler(common_callback, pattern="^common_")
    )

    await app.initialize()
    await app.start()

    await app.bot.delete_webhook(drop_pending_updates=True)

    logger.info("✅ Bot started successfully!")

    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        error_callback=lambda error: logger.error(
            f"Polling error: {error}"
        )
    )

    await monitor_loop(app.bot)

    await app.updater.stop()
    await app.stop()
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
