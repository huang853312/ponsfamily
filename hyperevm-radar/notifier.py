import os
import aiohttp

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

async def send_telegram(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "disable_web_page_preview": True
            },
            timeout=10,
        ) as resp:
            return resp.status == 200
