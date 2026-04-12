import asyncio
import logging
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import TELEGRAM_BOT_TOKEN, CHECK_INTERVAL
from database import init_db, add_wallet, remove_wallet, get_wallets_by_chat
from monitor import monitor_loop, fetch_positions, fetch_open_orders

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Hyperliquid Wallet Monitor*\n\n"
        "I notify you of any trades on wallets you track!\n\n"
        "📋 *Commands:*\n"
        "`/add <address> [label]` — Add a wallet\n"
        "`/remove <address>` — Remove a wallet\n"
        "`/list` — Show your wallets\n"
        "`/positions <address>` — View positions\n"
        "`/orders <address>` — View open orders\n"
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
            f"✅ Now monitoring:\n`{address}`{label_text}",
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

    lines = ["👛 *Your Monitored Wallets:*\n"]
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
        f"📊 *Positions:* `{address[:8]}...{address[-6:]}`",
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
            lines.append(
                f"{direction} *{coin}*\n"
                f"  Size: {abs(size)}\n"
                f"  Entry: ${entry:,.4f}\n"
                f"  {pnl_icon} uPnL: ${upnl:,.2f}\n"
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

    lines = [f"📋 *Open Orders:* `{address[:8]}...{address[-6:]}`\n"]
    for order in orders[:10]:
        side = "🟢 BUY" if order.get("side") == "B" else "🔴 SELL"
        coin = order.get("coin", "?")
        size = order.get("sz", "0")
        price = order.get("limitPx", "0")
        lines.append(
            f"{side} *{coin}* — Size: {size} @ ${float(price):,.4f}\n"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown"
    )


async def main():
    # Initialize database
    await init_db()

    # Build application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register all command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("remove", remove_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("positions", positions_command))
    app.add_handler(CommandHandler("orders", orders_command))

    # Initialize and start the app
    await app.initialize()
    await app.start()

    # Start polling for telegram updates
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )

    logger.info("✅ Bot started successfully!")

    # Start the wallet monitor loop (runs forever)
    await monitor_loop(app.bot)

    # Cleanup on exit
    await app.updater.stop()
    await app.stop()
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())