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
from monitor import monitor_loop, fetch_positions

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
        "`/common` — Common Active Positions (Only Open)\n"
        "`/help` — Show this message\n\n"
        f"🕐 Time: Nepal Time (UTC+5:45)"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def common_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("⏰ 1 Hour", callback_data="common_1"),
         InlineKeyboardButton("🕓 4 Hours", callback_data="common_4")],
        [InlineKeyboardButton("📅 1 Day", callback_data="common_24"),
         InlineKeyboardButton("📆 7 Days", callback_data="common_168")],
        [InlineKeyboardButton("🗓️ 30 Days", callback_data="common_720")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🔍 *COMMON ACTIVE POSITIONS*\n\n"
        "Shows coins where **2 or more** of your wallets\n"
        "currently have **open positions**.\n\n"
        "Closed positions are ignored.\n\n"
        "👇 Select time range (for trade history):",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


async def common_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    try:
        hours = int(query.data.split("_")[1])
    except Exception:
        hours = 1

    time_labels = {1: "1 Hour", 4: "4 Hours", 24: "1 Day", 168: "7 Days", 720: "30 Days"}
    time_label = time_labels.get(hours, f"{hours} Hours")

    await query.edit_message_text(
        f"⏳ Checking currently open common positions...\n"
        f"(Last {time_label} trade history considered)",
        parse_mode="Markdown"
    )

    wallets = await get_wallets_with_labels_by_chat(chat_id)
    if len(wallets) < 2:
        await query.edit_message_text("⚠️ You need at least 2 wallets to see common activity.", parse_mode="Markdown")
        return

    coin_data = defaultdict(list)

    async with aiohttp.ClientSession() as session:
        for address, label in wallets:
            wallet_name = label.upper() if label else f"{address[:6]}...{address[-4:]}"

            try:
                data = await fetch_positions(session, address)
                positions = data.get("assetPositions", [])

                for p in positions:
                    pos = p.get("position", {})
                    coin = pos.get("coin")
                    size = float(pos.get("szi", 0) or 0)
                    if not coin or size == 0:
                        continue  # Skip closed positions

                    entry = float(pos.get("entryPx", 0))
                    upnl = float(pos.get("unrealizedPnl", 0))
                    liq = float(pos.get("liquidationPx", 0))
                    leverage = 1
                    try:
                        lev = pos.get("leverage", {})
                        leverage = int(lev.get("value", 1)) if isinstance(lev, dict) else int(lev or 1)
                    except:
                        pass

                    pos_value = abs(size) * entry

                    coin_data[coin].append({
                        "name": wallet_name,
                        "side": "LONG" if size > 0 else "SHORT",
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

    # Only keep coins where 2 or more wallets have OPEN positions
    active_common = {coin: traders for coin, traders in coin_data.items() if len(traders) >= 2}

    if not active_common:
        await query.edit_message_text(
            f"📭 No common **active** positions found.\n\n"
            f"No coin is currently held open by 2 or more of your wallets.",
            parse_mode="Markdown"
        )
        return

    # Sort by most wallets
    sorted_coins = sorted(active_common.items(), key=lambda x: len(x[1]), reverse=True)

    msg = f"👁️ *COMMON ACTIVE POSITIONS*\n"
    msg += f"⏰ Last {time_label} History\n"
    msg += f"👛 {len(wallets)} Wallets Monitored\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

    for coin, traders in sorted_coins:
        msg += f"🪙 *{coin}/USDC* — **{len(traders)}** Wallets Holding\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"

        for t in traders:
            pnl_str = f"🟢 +${t['upnl']:,.2f}" if t['upnl'] >= 0 else f"🔴 -${abs(t['upnl']):,.2f}"
            msg += f"{t['emoji']} *{t['side']}* — *{t['name']}*\n"
            msg += f"   📦 `{t['size']:.4f}` @ `${t['entry']:,.4f}`\n"
            msg += f"   ⚡ Leverage: `{t['leverage']}x`\n"
            msg += f"   📊 Position: `${t['value']:,.2f}`\n"
            msg += f"   💰 PnL: {pnl_str}\n"
            if t['liq'] > 0:
                msg += f"   💀 Liq Price: `${t['liq']:,.4f}`\n"
            msg += "\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📅 {now_nepal()} (NPT)"

    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True)


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
    app.add_handler(CallbackQueryHandler(common_callback, pattern="^common_"))

    await app.initialize()
    await app.start()
    await app.bot.delete_webhook(drop_pending_updates=True)

    logger.info("✅ Bot started successfully!")
    await app.updater.start_polling(drop_pending_updates=True)
    await monitor_loop(app.bot)


if __name__ == "__main__":
    asyncio.run(main())
