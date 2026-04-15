import asyncio
import logging
import aiohttp
import time
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from config import TELEGRAM_BOT_TOKEN, CHECK_INTERVAL
from database import init_db, add_wallet, remove_wallet, get_wallets_by_chat, get_wallets_with_labels_by_chat
from monitor import monitor_loop, fetch_positions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
NEPAL_OFFSET = timedelta(hours=5, minutes=45)

def now_nepal():
    return (datetime.utcnow() + NEPAL_OFFSET).strftime('%Y-%m-%d %H:%M:%S')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👁️ *MONITOR ACTIVE*\n/add, /list, /common, /search, /wallet", parse_mode="Markdown")

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    addr = context.args[0].lower()
    lbl = context.args[1] if len(context.args) > 1 else None
    if await add_wallet(update.effective_chat.id, addr, lbl):
        await update.message.reply_text(f"✅ Added {addr}")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallets = await get_wallets_by_chat(update.effective_chat.id)
    if not wallets: return await update.message.reply_text("📭 Empty")
    msg = "\n".join([f"• `{w[0]}` ({w[1]})" for w in wallets])
    await update.message.reply_text(f"👛 *Wallets:*\n{msg}", parse_mode="Markdown")

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("Usage: /search BTC")
    coin = context.args[0].upper()
    wallets = await get_wallets_with_labels_by_chat(update.effective_chat.id)
    msg = f"🔍 *Search: {coin}*\n━━━━━━━━━━━━━━\n"
    found = False
    
    async with aiohttp.ClientSession() as session:
        for addr, lbl in wallets:
            data = await fetch_positions(session, addr)
            if not data: continue
            for p in data.get("assetPositions", []):
                pos = p['position']
                if pos['coin'] == coin and float(pos['szi']) != 0:
                    found = True
                    side = "🟢 LONG" if float(pos['szi']) > 0 else "🔴 SHORT"
                    msg += f"👛 *{lbl or addr[:6]}*\n{side} | Val: ${abs(float(pos['szi'])*float(pos['entryPx'])):,.2f}\nEntry: {pos['entryPx']}\n\n"
    
    if not found: msg += "No active positions found."
    await update.message.reply_text(msg, parse_mode="Markdown")

async def common_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("Check Now", callback_data="common_check")]]
    await update.message.reply_text("🔍 Check common active positions?", reply_markup=InlineKeyboardMarkup(kb))

async def common_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⏳ Scanning live positions...")
    
    wallets = await get_wallets_with_labels_by_chat(query.message.chat_id)
    coin_data = defaultdict(list)
    
    async with aiohttp.ClientSession() as session:
        for addr, lbl in wallets:
            data = await fetch_positions(session, addr)
            if not data: continue
            for p in data.get("assetPositions", []):
                pos = p['position']
                if float(pos['szi']) != 0:
                    coin_data[pos['coin']].append({"name": lbl or addr[:6], "side": "LONG" if float(pos['szi']) > 0 else "SHORT", "val": abs(float(pos['szi'])*float(pos['entryPx']))})

    common = {k: v for k, v in coin_data.items() if len(v) >= 2}
    if not common: return await query.edit_message_text("📭 No common active positions.")
    
    res = "👁️ *COMMON ACTIVE*\n━━━━━━━━━━━━━━\n"
    for coin, traders in common.items():
        res += f"🪙 *{coin}* ({len(traders)} wallets)\n"
        for t in traders:
            res += f"• {t['name']}: {t['side']} (${t['val']:,.0f})\n"
        res += "\n"
    await query.edit_message_text(res, parse_mode="Markdown")

async def main():
    await init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("common", common_command))
    app.add_handler(CallbackQueryHandler(common_callback, pattern="common_check"))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await monitor_loop(app.bot)

if __name__ == "__main__":
    asyncio.run(main())
