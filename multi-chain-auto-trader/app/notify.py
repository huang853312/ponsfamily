import json, logging, os, urllib.request
log = logging.getLogger(__name__)

def notify(text: str) -> bool:
    token = os.getenv("TRADER_TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TRADER_TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.warning("Telegram credentials not configured; notification skipped")
        return False
    body = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            return 200 <= response.status < 300
    except Exception:
        log.exception("Telegram notification failed")
        return False
