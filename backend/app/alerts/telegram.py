"""Optional Telegram alerts with strict de-duplication across state transitions."""
import logging
import time
import aiohttp
from app.config import settings, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from app.db import record_alert

log = logging.getLogger("alerts")

NOTIFY_STATUSES = {
    "LONG WATCH", "SHORT WATCH",
    "LONG DEVELOPING", "SHORT DEVELOPING",
    "LONG CONFIRMED", "SHORT CONFIRMED",
    "INVALIDATED",
}


async def send_telegram_raw(text: str) -> tuple[bool, str]:
    token = settings.telegram_bot_token or TELEGRAM_BOT_TOKEN
    chat_id = settings.telegram_chat_id or TELEGRAM_CHAT_ID
    if not (settings.telegram_enabled and token and chat_id):
        return False, "Telegram disabled or credentials not configured"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as sess:
            async with sess.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}) as r:
                if r.status == 200:
                    return True, "sent"
                body = await r.text()
                return False, f"HTTP {r.status}: {body[:120]}"
    except Exception as exc:
        return False, str(exc)


async def maybe_emit_alert(symbol: str, prev_status: str | None, new_state: dict, price: float):
    status = new_state.get("status", "NO SETUP")
    early = new_state.get("early_watch", False)

    # Emit either on meaningful state transition OR first entry into EARLY WATCH
    trigger_status = status
    if status == "NO SETUP" and early:
        trigger_status = "EARLY WATCH"
    elif status not in NOTIFY_STATUSES:
        return

    if prev_status == trigger_status:
        return  # already in this state — prevent duplicate spam

    side = new_state.get("active_side", "")
    ez = new_state.get("entry_zone")
    inv = new_state.get("invalidation")
    tgts = new_state.get("targets") or []
    rr = new_state.get("rr", 0)
    # Dedup bucket per symbol + status + rounded entry zone (1-hour bucket for EARLY WATCH)
    bucket = f"{ez[0]:.5g}-{ez[1]:.5g}" if ez else str(int(time.time()) // 3600)
    dedup_key = f"{symbol}:{trigger_status}:{bucket}"

    msg = (
        f"<b>[{trigger_status}] {symbol}</b> @ {price:.6g}\n"
        f"Long Tech/Opp: {new_state['long_tech_score']} / {new_state['long_opp_score']} | "
        f"Short Tech/Opp: {new_state['short_tech_score']} / {new_state['short_opp_score']}\n"
    )
    if ez:
        msg += f"Entry Zone: {ez[0]:.6g} – {ez[1]:.6g} | Inv: {inv:.6g} | R:R {rr} | Targets: {', '.join(f'{t:.6g}' for t in tgts)}\n"
    if new_state.get("early_reasons"):
        msg += "Early signals: " + "; ".join(new_state["early_reasons"])

    sent, _ = await send_telegram_raw(msg)
    await record_alert(int(time.time() * 1000), symbol, trigger_status, side, price, msg.replace("<b>", "").replace("</b>", ""), dedup_key, sent)
