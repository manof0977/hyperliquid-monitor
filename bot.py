import asyncio
import logging
import aiohttp
import time
from datetime import datetime, timezone
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.error import Conflict, NetworkError
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
        "`/common` — Common trades menu\n"
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
            "📭 No wallets monitored yet.\nUse `/add <address>` to start!",
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
    account_value = data.get("marginSummary", {}).get("accountValue", "0")

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
            f"📋 No open orders for `{address[:8]}...{address[-6:]}`",
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


# ─── Common Trades ───────────────────────────────────────────────────


async def common_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show time range buttons when user types /common"""

    keyboard = [
        [
            InlineKeyboardButton("⏰ 1 Hour", callback_data="common_1"),
            InlineKeyboardButton("🕓 4 Hours", callback_data="common_4"),
        ],
        [
            InlineKeyboardButton("📅 1 Day", callback_data="common_24"),
            InlineKeyboardButton("📆 7 Days", callback_data="common_168"),
        ],
        [
            InlineKeyboardButton("🗓️ 30 Days", callback_data="common_720"),
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🔍 *COMMON TRADES*\n\n"
        "Select time range to analyze:\n\n"
        "👇 Tap a button below",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


async def common_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button press for common trades time range"""

    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # Get hours from callback data
    # Format: common_<hours>
    try:
        hours = int(query.data.split("_")[1])
    except Exception:
        hours = 1

    # Label for display
    labels_map = {
        1: "1 Hour",
        4: "4 Hours",
        24: "1 Day",
        168: "7 Days",
        720: "30 Days"
    }
    time_label = labels_map.get(hours, f"{hours} Hours")

    await query.edit_message_text(
        f"⏳ Analyzing wallets for last *{time_label}*...",
        parse_mode="Markdown"
    )

    # Get all wallets for this chat
    wallets = await get_wallets_with_labels_by_chat(chat_id)

    if not wallets:
        await query.edit_message_text(
            "📭 No wallets monitored yet.\nUse `/add <address>` to start!",
            parse_mode="Markdown"
        )
        return

    if len(wallets) < 2:
        await query.edit_message_text(
            "⚠️ You need at least *2 wallets* to compare!\n"
            "Add more wallets with `/add <address>`",
            parse_mode="Markdown"
        )
        return

    total_wallets = len(wallets)

    # Time range in milliseconds
    now = int(time.time() * 1000)
    since = now - (hours * 60 * 60 * 1000)

    # Fetch trades for all wallets
    # {coin_side_key: {coin, side, wallets[]}}
    coin_side_trades = defaultdict(lambda: {
        "coin": "",
        "side": "",
        "wallets": []
    })

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

                # Filter by time range
                recent = [
                    t for t in trades
                    if t.get("time", 0) >= since
                ]

                # Track unique coins per wallet
                # to avoid duplicate entries
                seen_coin_side = set()

                for trade in recent:
                    coin = trade.get("coin", "")
                    side = trade.get("side", "")
                    price = float(trade.get("px", 0))
                    size = float(trade.get("sz", 0))
                    order_value = price * size
                    trade_time = trade.get("time", 0)

                    key = f"{coin}_{side}"

                    coin_side_trades[key]["coin"] = coin
                    coin_side_trades[key]["side"] = side

                    # Only add wallet once per coin+side
                    if key not in seen_coin_side:
                        seen_coin_side.add(key)
                        coin_side_trades[key]["wallets"].append({
                            "name": wallet_name,
                            "address": address,
                            "price": price,
                            "size": size,
                            "value": order_value,
                            "time": trade_time
                        })

            except Exception as e:
                logger.error(f"Error fetching {address}: {e}")
                continue

    # Filter only coins where 2+ wallets traded
    common = []
    for key, data in coin_side_trades.items():
        wallet_list = data.get("wallets", [])
        if len(wallet_list) >= 2:
            common.append({
                "coin": data["coin"],
                "side": data["side"],
                "wallets": wallet_list,
                "count": len(wallet_list)
            })

    # Sort by most wallets
    common.sort(key=lambda x: x["count"], reverse=True)

    # ── Build Result Message ──────────────────────────────────

    if not common:
        await query.edit_message_text(
            f"📭 *No Common Trades Found*\n\n"
            f"No wallets made the same trade in last *{time_label}*\n\n"
            f"Try a longer time range!",
            parse_mode="Markdown"
        )
        return

    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

    msg = f"🔍 *COMMON TRADES*\n"
    msg += f"⏰ Last {time_label}\n"
    msg += f"👛 {total_wallets} Wallets Analyzed\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

    for item in common:
        coin = item["coin"]
        side = item["side"]
        wallets_in = item["wallets"]
        count = item["count"]

        # Direction
        if side == "B":
            direction = "LONG"
            dir_emoji = "🟢"
        else:
            direction = "SHORT"
            dir_emoji = "🔴"

        # Agreement level
        agreement_pct = (count / total_wallets) * 100
        if agreement_pct >= 60:
            level = "🔥 HIGH"
        elif agreement_pct >= 40:
            level = "⚡ MODERATE"
        else:
            level = "👀 LOW"

        # Averages
        avg_price = sum(
            w["price"] for w in wallets_in
        ) / len(wallets_in)

        total_value = sum(w["value"] for w in wallets_in)

        msg += f"{level} AGREEMENT ({count}/{total_wallets})\n"
        msg += f"{dir_emoji} *{direction} — {coin}/USDC*\n"

        for w in wallets_in:
            trade_time_str = datetime.utcfromtimestamp(
                w["time"] / 1000
            ).strftime('%d/%m %H:%M')
            msg += (
                f"  👛 {w['name']}\n"
                f"      {w['size']:.4f} @ "
                f"${w['price']:,.4f} [{trade_time_str}]\n"
            )

        msg += f"📊 Avg Price: ${avg_price:,.4f}\n"
        msg += f"💵 Combined: ${total_value:,.2f}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

    msg += f"📅 {now_str} (UTC+0)"

    # Trim if too long
    if len(msg) > 4000:
        msg = msg[:3900] + "\n\n_...more results truncated_"

    await query.edit_message_text(
        msg,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


async def main():
    await init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("remove", remove_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("positions", positions_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("common", common_command))

    # Button callback handler
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
        error_callback=lambda error: logger.error(f"Polling error: {error}")
    )

    await monitor_loop(app.bot)

    await app.updater.stop()
    await app.stop()
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
