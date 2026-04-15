import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta
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
    except:
        return "Unknown Time"

async def fetch_info(session, payload):
    try:
        async with session.post(HYPERLIQUID_API, json=payload, headers=HEADERS, timeout=10) as resp:
            if resp.status == 200:
                return await resp.json()
            return None
    except Exception as e:
        logger.error(f"API Error: {e}")
        return None

async def fetch_trades(session, address):
    return await fetch_info(session, {"type": "userFills", "user": address})

async def fetch_positions(session, address):
    return await fetch_info(session, {"type": "clearinghouseState", "user": address})

async def fetch_open_orders(session, address):
    return await fetch_info(session, {"type": "openOrders", "user": address})

def calculate_leverage(pos):
    try:
        lev = pos.get("leverage", {})
        if isinstance(lev, dict): return int(lev.get("value", 1))
        return int(lev or 1)
    except: return 1

def determine_action(side, pos_before, pos_after):
    direction = "LONG" if (pos_after > 0 or (pos_after == 0 and pos_before > 0)) else "SHORT"
    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    
    if pos_before == 0 and pos_after != 0: action = "Open"
    elif pos_after == 0 and pos_before != 0: action = "Close"
    elif abs(pos_after) > abs(pos_before): action = "Increase"
    elif abs(pos_after) < abs(pos_before): action = "Reduce"
    else: action = "Update"
    
    return action, direction, dir_emoji, f"Cross-{direction.capitalize()}-{action}"

def format_trade_message(trade, address, label, position_after=None, position_before_size=0):
    coin = trade.get("coin", "Unknown")
    side = trade.get("side", "B")
    sz = float(trade.get("sz", 0))
    px = float(trade.get("px", 0))
    
    pos = position_after or {}
    pos_sz = float(pos.get("szi", 0) or 0)
    entry = float(pos.get("entryPx", 0) or px)
    upnl = float(pos.get("unrealizedPnl", 0) or 0)
    liq = float(pos.get("liquidationPx", 0) or 0)
    lev = calculate_leverage(pos)
    
    action, direction, emoji, act_str = determine_action(side, position_before_size, pos_sz)
    wallet_name = label.upper() if label else f"{address[:8]}..."
    
    msg = f"👁️ *{wallet_name}*\n👛 `{address}`\n━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"🔔 *ACTION — {emoji} {direction}*\n✳️ {coin}/USDC ({act_str})\n"
    msg += f"• Quantity: {position_before_size:.4f} → {pos_sz:.4f}\n• Price: ${px:.4f}\n\n📊 *POSITION*\n"
    
    if pos_sz != 0:
        pnl_emoji = "🟢" if upnl >= 0 else "🔴"
        msg += f"• Value: ${abs(pos_sz)*entry:,.2f}\n• Entry: ${entry:.4f}\n• Lev: {lev}x\n"
        if liq > 0: msg += f"• Liq: ${liq:.4f}\n"
        msg += f"• PnL: {pnl_emoji} ${upnl:,.2f}\n"
    else:
        msg += "• Position Closed\n"
        
    msg += f"\n📅 {to_nepal_time(trade.get('time', 0))} (NPT)"
    return msg

async def message_queue_worker(bot):
    while True:
        item = await message_queue.get()
        try:
            await bot.send_message(chat_id=item["chat_id"], text=item["message"], parse_mode="Markdown", disable_web_page_preview=True)
            await asyncio.sleep(MESSAGE_DELAY)
        except Exception as e:
            logger.error(f"Queue Error: {e}")
        message_queue.task_done()

async def check_wallet(session, address, chat_ids, labels, bot):
    global previous_positions
    pos_data = await fetch_positions(session, address)
    if not pos_data: return
    
    asset_positions = pos_data.get("assetPositions", [])
    current_pos_map = {p['position']['coin']: float(p['position']['szi']) for p in asset_positions}
    prev_map = previous_positions.get(address, {})
    
    trades = await fetch_trades(session, address)
    last_time = await get_last_trade_time(address)
    
    if trades:
        new_trades = [t for t in trades if t.get("time", 0) > last_time]
        if new_trades:
            new_trades.sort(key=lambda x: x.get("time", 0))
            await update_last_trade_time(address, new_trades[-1]["time"])
            
            for t in new_trades:
                coin = t.get("coin")
                msg = format_trade_message(t, address, labels.get(address), 
                                         next((p['position'] for p in asset_positions if p['position']['coin'] == coin), None),
                                         prev_map.get(coin, 0))
                for cid in chat_ids:
                    await message_queue.put({"chat_id": cid, "message": msg})
                    
    previous_positions[address] = current_pos_map

async def monitor_loop(bot):
    asyncio.create_task(message_queue_worker(bot))
    async with aiohttp.ClientSession() as session:
        while True:
            wallets = await get_all_wallets()
            if wallets:
                w_map = defaultdict(list)
                l_map = {}
                for addr, cid, lbl in wallets:
                    w_map[addr].append(cid)
                    l_map[addr] = lbl
                await asyncio.gather(*[check_wallet(session, addr, cids, l_map, bot) for addr, cids in w_map.items()])
            await asyncio.sleep(CHECK_INTERVAL)
