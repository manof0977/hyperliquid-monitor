# Updated `bot.py` — Show ONLY Active Common Positions

This update changes the `/common` command to **ignore closed positions**. It will only show coins where 2 or more wallets are **currently holding** an open position. It also uses your current position side (Long/Short), leverage, size, and PnL.

---

## Update `bot.py` on GitHub

**Go to GitHub → Click `bot.py` → Click pencil ✏️**

**Select ALL → Delete → Paste this entire code:**

```python
import asyncio
import logging
import aiohttp
import time
from datetime import datetime, timezone, timedelta
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

# Nepal Time = UTC + 5:45
NEPAL_OFFSET = timedelta(hours=5, minutes=45)


def to_nepal_time(utc_timestamp_ms):
    """Convert UTC millisecond timestamp to Nepal time string"""
    utc_dt = datetime.utcfromtimestamp(utc_timestamp_ms / 1000)
    nepal_dt = utc_dt + NEPAL_OFFSET
    return nepal_dt.strftime('%Y-%m-%d %H:%M:%S')


def now_nepal():
    """Get current Nepal time string"""
    utc_now = datetime.utcnow()
    nepal_now = utc_now + NEPAL_OFFSET
    return nepal_now.strftime('%Y-%m-%d %H:%M:%S')


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
        "`/common` — Common active positions\n"
        "`/help` — Show this message\n\n"
        f"🕐 Time: Nepal Time (UTC+5:45)\n"
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
            liq = float(pos.get("liquidationPx", 0) or 0)
            direction = "🟢 LONG" if size > 0 else "🔴 SHORT"
            pnl_icon = "📈" if upnl >= 0 else "📉"
            pnl_sign = "+" if upnl >= 0 else ""
            lines.append(
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{direction} *{coin}*\n"
                f"• Size: {abs(size)}\n"
                f"• Entry: ${entry:,.4f}\n"
                f"• Liq: ${liq:,.4f}\n"
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


# ─── Common Active Positions ─────────────────────────────────


async def common_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
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
        "🔍 *COMMON ACTIVE COINS*\n\n"
        "Select time range to analyze recent trades.\n"
        "Shows coins where multiple wallets **STILL hold an open position**.\n\n"
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


def calculate_leverage(position):
    """Calculate leverage from position data"""
    try:
        leverage = position.get("leverage", {})
        if isinstance(leverage, dict):
            val = leverage.get("value", 1)
            return int(val) if val else 1
        return int(leverage) if leverage else 1
    except Exception:
        return 1


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
                    price = float(trade.get("px", 0))
                    trade_time = trade.get("time", 0)

                    if not coin:
                        continue

                    if coin not in seen_coins:
                        seen_coins.add(coin)

                        # Fetch live position data
                        pos = await fetch_wallet_position(
                            session, address, coin
                        )

                        if pos:
                            pos_size = float(pos.get("szi", 0) or 0)
                            
                            # 🚫 IGNORE IF POSITION IS CLOSED (SZI == 0)
                            if pos_size == 0:
                                continue

                            entry_price = float(pos.get("entryPx", 0) or price)
                            upnl = float(pos.get("unrealizedPnl", 0) or 0)
                            liq_price = float(pos.get("liquidationPx", 0) or 0)
                            leverage = calculate_leverage(pos)
                            pos_value = abs(pos_size) * entry_price
                            
                            # Use current position size for direction
                            side = "B" if pos_size > 0 else "A"

                            coin_activity[coin].append({
                                "name": wallet_name,
                                "address": address,
                                "side": side,
                                "price": price, # Last traded price
                                "size": abs(pos_size), # Active size
                                "time": trade_time,
                                "pos_value": pos_value,
                                "upnl": upnl,
                                "liq_price": liq_price,
                                "leverage": leverage,
                                "is_open": True,
                                "entry_price": entry_price
                            })

            except Exception as e:
                logger.error(f"Error fetching {address}: {e}")
                continue

    # Filter only coins where 2+ wallets have ACTIVE positions
    common_coins = {
        coin: traders
        for coin, traders in coin_activity.items()
        if len(traders) >= 2
    }

    if not common_coins:
        await query.edit_message_text(
            f"📭 *No Active Common Coins Found*\n\n"
            f"No 2+ wallets are currently holding the same coin.\n\n"
            f"Try a longer time range!",
            parse_mode="Markdown"
        )
        return

    # Sort by most wallets holding the coin
    sorted_coins = sorted(
        common_coins.items(),
        key=lambda x: len(x[1]),
        reverse=True
    )

    msg = f"👁️ *COMMON ACTIVE COIN ACTIVITY*\n"
    msg += f"⏰ Traded in last {time_label}\n"
    msg += f"👛 {total_wallets} Wallets Analyzed\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

    for coin, traders in sorted_coins:

        wallet_count = len(traders)
        longs = [t for t in traders if t["side"] == "B"]
        shorts = [t for t in traders if t["side"] == "A"]

        # Coin header
        msg += f"🪙 *{coin}/USDC*"
        msg += f" — {wallet_count}"
        msg += f" Wallet{'s' if wallet_count > 1 else ''} holding\n"

        # Long short split summary
        if longs and shorts:
            msg += (
                f"   🟢 {len(longs)} "
                f"Long{'s' if len(longs) > 1 else ''}"
                f" | 🔴 {len(shorts)} "
                f"Short{'s' if len(shorts) > 1 else ''}\n"
            )
        elif longs:
            msg += f"   🟢 All Longs\n"
        elif shorts:
            msg += f"   🔴 All Shorts\n"

        msg += f"━━━━━━━━━━━━━━━━━━━━\n"

        # Longs first then Shorts
        all_traders = []
        for t in longs:
            all_traders.append((t, "LONG", "🟢"))
        for t in shorts:
            all_traders.append((t, "SHORT", "🔴"))

        for trader, direction, emoji in all_traders:

            # Convert to Nepal time
            nepal_time = to_nepal_time(trader["time"])

            upnl = trader["upnl"]
            pos_value = trader["pos_value"]
            liq_price = trader["liq_price"]
            leverage = trader["leverage"]

            if upnl > 0:
                pnl_str = f"🟢 +${upnl:,.2f}"
            elif upnl < 0:
                pnl_str = f"🔴 -${abs(upnl):,.2f}"
            else:
                pnl_str = f"⚪ $0.00"

            msg += f"{emoji} *{direction}* — *{trader['name']}*\n"
            msg += (
                f"   📦 `{trader['size']:.4f} {coin}`"
                f" @ `${trader['entry_price']:,.4f}`\n"
            )
            msg += f"   ⚡ Leverage: `{leverage}x`\n"
            msg += f"   📊 Position: `${pos_value:,.2f}`\n"
            msg += f"   💰 PnL: {pnl_str}\n"
            if liq_price and liq_price > 0:
                msg += f"   💀 Liq: `${liq_price:,.4f}`\n"

            msg += f"   🕐 `{nepal_time} (NPT)`\n\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📅 {now_nepal()} (NPT)"

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
```

**Commit changes** ✅

---

### Key changes made:
1. It now checks the live position size `pos_size` of a coin traded in that period.
2. If `pos_size == 0` (meaning they scalped it and closed it), it skips it.
3. It only shows coins where **2+ people are holding a live position**. All output will always be live and active!
