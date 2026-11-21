import requests


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        if not token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def send(self, text: str):
        response = requests.post(
            self.url,
            json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API rejected message: {result}")
