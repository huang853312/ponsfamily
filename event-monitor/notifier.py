import requests


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, timeout: int = 15):
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, text: str) -> None:
        response = requests.post(
            self.url,
            json={"chat_id": self.chat_id, "text": text},
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API rejected message: {result}")
