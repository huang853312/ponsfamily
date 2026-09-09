import aiohttp

class Telegram:
    def __init__(self, token, chat_id): self.token, self.chat_id=token,chat_id
    @property
    def enabled(self): return bool(self.token and self.chat_id)
    async def send(self,text):
        if not self.enabled: return None
        url=f"https://api.telegram.org/bot{self.token}/sendMessage"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(url,json={"chat_id":self.chat_id,"text":text,"disable_web_page_preview":True}) as response:
                response.raise_for_status(); body=await response.json()
                if not body.get("ok"): raise RuntimeError(body)
                return body["result"]["message_id"]

