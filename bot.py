import asyncio
import logging
import aiohttp
import time
from datetime import datetime, timedelta
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

# Nepal Time (UTC + 5:45)
NEPAL_OFFSET = timedelta(hours=5, minutes=45)


def to_nepal_time(utc_timestamp_ms):
    utc_dt = datetime.utcfromtimestamp(utc_timestamp_ms / 1000)
    nepal_dt = utc_dt + NEPAL_OFFSET
    return nepal_dt.strftime('%Y-%m-%d %H:%M:%S')


def now_nepal():
    return (datetime.utcnow() + NEPAL_OFFSET).strftime('%Y-%m-%d %H:%M:%S')


def calculate_leverage(pos):
    try:
        lev = pos.get("leverage", {})
        if isinstance(lev, dict):
            return int(lev.get("value", 1))
        return int(lev) if lev else 1
    except Exception:
        return 1


# ─── Commands ─────────────────────────────────────────────────


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👁️ *HYPERLIQUID WALLET MONITOR*\n\n"
        "Track wallets and get instant trade alerts!\n\n"
        "📋 *Commands:*\n"
        "`/add <address> [label]` — Add a wallet\n"
        "`/remove <address>` — Remove a wallet\n"
        "`/list` — Show your wallets\n"
        "`/positions <address>` — View open positions\n"
        "`/orders <address>` — View open orders\n"
        "`/common` — Common active positions\n"
        "`/search <coin>` — Search coin across wallets\n"
        "`/help` — Show this message\n\n"
        "🕐 All times in Nepal Time (UTC+5:45)"
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
            "❌ Invalid address!\n"
            "Must start with `0x` and be 42 characters long.",
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
            liq = float(pos.get("liquidationPx", 0) or 0)
            lev = calculate_leverage(pos)
            direction = "🟢 LONG" if size > 0 else "🔴 SHORT"
            pnl_icon = "📈" if upnl >= 0 else "📉"
            pnl_sign = "+" if upnl >= 0 else ""
            lines.append(
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{direction} *{coin}*\n"
                f"• Size: {abs(size)}\n"
                f"• Entry: ${entry:,.4f}\n"
                f"• Liq: ${liq:,.4f}\n"
                f"• Leverage: {lev}x\n"
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


# ─── Search Command ───────────────────────────────────────────


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /search BTC
    Shows which monitored wallets have open positions in BTC
    """
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Usage: `/search <coin>`\n\n"
            "Examples:\n"
            "`/search BTC`\n"
            "`/search ETH`\n"
            "`/search SOL`",
            parse_mode="Markdown"
        )
        return

    # Clean coin input
    coin_query = args[0].upper().strip()

    # Remove /USDC if user typed it
    coin_query = coin_query.replace("/USDC", "").replace("USDC", "").strip()

    chat_id = update.effective_chat.id
    wallets = await get_wallets_with_labels_by_chat(chat_id)

    if not wallets:
        await update.message.reply_text(
            "📭 No wallets monitored yet.\n"
            "Use `/add <address>` to start!",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(
        f"🔍 Searching *{coin_query}* across "
        f"{len(wallets)} wallets...",
        parse_mode="Markdown"
    )

    results = []

    async with aiohttp.ClientSession() as session:
        for address, label in wallets:
            wallet_name = (
                label.upper() if label
                else f"{address[:6]}...{address[-4:]}"
            )

            try:
                data = await fetch_positions(session, address)
                positions = data.get("assetPositions", [])

                for p in positions:
                    pos = p.get("position", {})
                    coin = pos.get("coin", "")
                    size = float(pos.get("szi", 0) or 0)

                    # Match coin and only show open positions
                    if coin.upper() != coin_query or size == 0:
                        continue

                    entry = float(pos.get("entryPx", 0) or 0)
                    upnl = float(pos.get("unrealizedPnl", 0) or 0)
                    liq = float(pos.get("liquidationPx", 0) or 0)
                    lev = calculate_leverage(pos)
                    pos_value = abs(size) * entry
                    direction = "LONG" if size > 0 else "SHORT"
                    emoji = "🟢" if size > 0 else "🔴"

                    results.append({
                        "name": wallet_name,
                        "address": address,
                        "direction": direction,
                        "emoji": emoji,
                        "size": abs(size),
                        "entry": entry,
                        "value": pos_value,
                        "upnl": upnl,
                        "liq": liq,
                        "leverage": lev
                    })

            except Exception as e:
                logger.error(f"Error fetching {address}: {e}")
                continue

    # No results found
    if not results:
        await update.message.reply_text(
            f"📭 *No Open Positions Found*\n\n"
            f"None of your monitored wallets\n"
            f"currently have an open *{coin_query}* position.",
            parse_mode="Markdown"
        )
        return

    # Sort — Longs first then Shorts
    longs = [r for r in results if r["direction"] == "LONG"]
    shorts = [r for r in results if r["direction"] == "SHORT"]

    # Build message
    msg = f"🔍 *SEARCH: {coin_query}/USDC*\n"
    msg += f"👛 {len(wallets)} Wallets Checked\n"
    msg += f"📊 {len(results)} Open Position"
    msg += f"{'s' if len(results) > 1 else ''} Found\n"

    if longs and shorts:
        msg += (
            f"🟢 {len(longs)} Long"
            f"{'s' if len(longs) > 1 else ''}"
            f" | 🔴 {len(shorts)} Short"
            f"{'s' if len(shorts) > 1 else ''}\n"
        )
    elif longs:
        msg += f"🟢 All Longs\n"
    else:
        msg += f"🔴 All Shorts\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

    # Show all longs first
    if longs:
        msg += f"🟢 *LONG POSITIONS*\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        for r in longs:
            pnl = r["upnl"]
            pnl_str = (
                f"🟢 +${pnl:,.2f}"
                if pnl >= 0
                else f"🔴 -${abs(pnl):,.2f}"
            )
            msg += f"👛 *{r['name']}*\n"
            msg += f"   📦 Size: `{r['size']:.4f} {coin_query}`\n"
            msg += f"   🎯 Entry: `${r['entry']:,.4f}`\n"
            msg += f"   📊 Position: `${r['value']:,.2f}`\n"
            msg += f"   ⚡ Leverage: `{r['leverage']}x`\n"
            if r["liq"] > 0:
                msg += f"   💀 Liq: `${r['liq']:,.4f}`\n"
            msg += f"   💰 PnL: {pnl_str}\n\n"

    # Then all shorts
    if shorts:
        msg += f"🔴 *SHORT POSITIONS*\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        for r in shorts:
            pnl = r["upnl"]
            pnl_str = (
                f"🟢 +${pnl:,.2f}"
                if pnl >= 0
                else f"🔴 -${abs(pnl):,.2f}"
            )
            msg += f"👛 *{r['name']}*\n"
            msg += f"   📦 Size: `{r['size']:.4f} {coin_query}`\n"
            msg += f"   🎯 Entry: `${r['entry']:,.4f}`\n"
            msg += f"   📊 Position: `${r['value']:,.2f}`\n"
            msg += f"   ⚡ Leverage: `{r['leverage']}x`\n"
            if r["liq"] > 0:
                msg += f"   💀 Liq: `${r['liq']:,.4f}`\n"
            msg += f"   💰 PnL: {pnl_str}\n\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📅 {now_nepal()} (NPT)"

    await update.message.reply_text(
        msg,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


# ─── Common Active Positions ──────────────────────────────────


async def common_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    keyboard = [
        [
            InlineKeyboardButton("⏰ 1 Hour", callback_data="common_1"),
            InlineKeyboardButton("🕓 4 Hours", callback_data="common_4")
        ],
        [
            InlineKeyboardButton("📅 1 Day", callback_data="common_24"),
            InlineKeyboardButton("📆 7 Days", callback_data="common_168")
        ],
        [
            InlineKeyboardButton("🗓️ 30 Days", callback_data="common_720")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🔍 *COMMON ACTIVE POSITIONS*\n\n"
        "Shows coins where *2 or more* wallets\n"
        "currently have *open positions*\n\n"
        "Closed positions are *ignored*\n\n"
        "👇 Select time range:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


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

    time_labels = {
        1: "1 Hour",
        4: "4 Hours",
        24: "1 Day",
        168: "7 Days",
        720: "30 Days"
    }
    time_label = time_labels.get(hours, f"{hours} Hours")

    await query.edit_message_text(
        f"⏳ Checking currently open positions...",
        parse_mode="Markdown"
    )

    wallets = await get_wallets_with_labels_by_chat(chat_id)

    if not wallets:
        await query.edit_message_text(
            "📭 No wallets monitored yet.",
            parse_mode="Markdown"
        )
        return

    if len(wallets) < 2:
        await query.edit_message_text(
            "⚠️ You need at least *2 wallets* to compare!",
            parse_mode="Markdown"
        )
        return

    coin_data = defaultdict(list)

    async with aiohttp.ClientSession() as session:
        for address, label in wallets:
            wallet_name = (
                label.upper() if label
                else f"{address[:6]}...{address[-4:]}"
            )

            try:
                data = await fetch_positions(session, address)
                positions = data.get("assetPositions", [])

                for p in positions:
                    pos = p.get("position", {})
                    coin = pos.get("coin", "")
                    size = float(pos.get("szi", 0) or 0)

                    if not coin or size == 0:
                        continue

                    entry = float(pos.get("entryPx", 0) or 0)
                    upnl = float(pos.get("unrealizedPnl", 0) or 0)
                    liq = float(pos.get("liquidationPx", 0) or 0)
                    leverage = calculate_leverage(pos)
                    pos_value = abs(size) * entry

                    coin_data[coin].append({
                        "name": wallet_name,
                        "direction": "LONG" if size > 0 else "SHORT",
                        "emoji": "🟢" if size > 0 else "🔴",
                        "size": abs(size),
                        "entry": entry,
                        "value": pos_value,
                        "upnl": upnl,
                        "liq": liq,
                        "leverage": leverage
                    })

            except Exception as e:
                logger.error(f"Error fetching {address}: {e}")

    common_coins = {
        coin: traders
        for coin, traders in coin_data.items()
        if len(traders) >= 2
    }

    if not common_coins:
        await query.edit_message_text(
            "📭 *No Common Active Positions*\n\n"
            "No coin is currently held open\n"
            "by 2 or more of your wallets.",
            parse_mode="Markdown"
        )
        return

    sorted_coins = sorted(
        common_coins.items(),
        key=lambda x: len(x[1]),
        reverse=True
    )

    msg = f"👁️ *COMMON ACTIVE POSITIONS*\n"
    msg += f"👛 {len(wallets)} Wallets Monitored\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

    for coin, traders in sorted_coins:
        longs = [t for t in traders if t["direction"] == "LONG"]
        shorts = [t for t in traders if t["direction"] == "SHORT"]

        msg += f"🪙 *{coin}/USDC* — *{len(traders)}* Wallet"
        msg += f"{'s' if len(traders) > 1 else ''} Open\n"

        if longs and shorts:
            msg += (
                f"   🟢 {len(longs)} Long"
                f"{'s' if len(longs) > 1 else ''}"
                f" | 🔴 {len(shorts)} Short"
                f"{'s' if len(shorts) > 1 else ''}\n"
            )
        elif longs:
            msg += f"   🟢 All Longs\n"
        else:
            msg += f"   🔴 All Shorts\n"

        msg += f"━━━━━━━━━━━━━━━━━━━━\n"

        all_traders = (
            [(t, "🟢") for t in longs] +
            [(t, "🔴") for t in shorts]
        )

        for t, em in all_traders:
            pnl = t["upnl"]
            pnl_str = (
                f"🟢 +${pnl:,.2f}"
                if pnl >= 0
                else f"🔴 -${abs(pnl):,.2f}"
            )
            msg += f"{em} *{t['direction']}* — *{t['name']}*\n"
            msg += f"   📦 `{t['size']:.4f}` @ `${t['entry']:,.4f}`\n"
            msg += f"   ⚡ Leverage: `{t['leverage']}x`\n"
            msg += f"   📊 Position: `${t['value']:,.2f}`\n"
            msg += f"   💰 PnL: {pnl_str}\n"
            if t["liq"] and t["liq"] > 0:
                msg += f"   💀 Liq: `${t['liq']:,.4f}`\n"
            msg += "\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📅 {now_nepal()} (NPT)"

    if len(msg) > 4000:
        msg = msg[:3900] + "\n\n_...truncated_"

    await query.edit_message_text(
        msg,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


# ─── Main ─────────────────────────────────────────────────────


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
    app.add_handler(CommandHandler("search", search_command))
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
