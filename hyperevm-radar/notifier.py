import asyncio
import os

import aiohttp

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_MAX_ATTEMPTS = 3
TELEGRAM_RETRY_BASE_SECONDS = 1.0


class TelegramDeliveryError(RuntimeError):
    """Raised when a Telegram alert could not be delivered."""


async def send_telegram(text: str):
    """Send one Telegram message and fail loudly after bounded retries.

    The caller already catches exceptions and writes them to service logs, so this
    function must never return a silent False for configuration/API failures.
    """
    if not BOT_TOKEN or not CHAT_ID:
        raise TelegramDeliveryError(
            "Telegram configuration missing: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID"
        )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }

    last_error = "unknown Telegram delivery failure"

    async with aiohttp.ClientSession() as session:
        for attempt in range(1, TELEGRAM_MAX_ATTEMPTS + 1):
            try:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    body = (await resp.text())[:500]
                    if resp.status == 200:
                        return True

                    last_error = (
                        f"Telegram API HTTP {resp.status} on attempt "
                        f"{attempt}/{TELEGRAM_MAX_ATTEMPTS}: {body}"
                    )

                    # Do not retry permanent client/configuration failures.
                    if 400 <= resp.status < 500 and resp.status != 429:
                        break

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = (
                    f"Telegram transport failure on attempt "
                    f"{attempt}/{TELEGRAM_MAX_ATTEMPTS}: "
                    f"{type(exc).__name__}: {exc}"
                )

            if attempt < TELEGRAM_MAX_ATTEMPTS:
                await asyncio.sleep(TELEGRAM_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))

    raise TelegramDeliveryError(last_error)


def format_family_intelligence_message(result, family):
    """Human-first Family alert; contract addresses remain backend evidence."""
    types = " / ".join(result.get("infrastructure_types") or ["Other"])
    token = "暂未发现官方平台代币"
    if result.get("token_status") == "CONFIRMED_OFFICIAL":
        symbol = result.get("official_token_symbol") or "Unknown symbol"
        token = f"{symbol}\nCA：{result.get('official_token_ca')}"
    reason = (
        f"同一 Creator 的 {int(family.get('member_count', 0) or 0)} 个合约形成 "
        f"{family.get('platform_types') or '基础设施'} Family"
    )
    sources = sorted({str(x.get("source")) for x in result.get("evidence", []) if x.get("source")})
    return (
        "【HyperEVM 新基础设施】\n\n"
        f"项目：{result.get('project_name') or 'Unknown'}\n"
        f"类型：{types}\n"
        f"做什么：{result.get('description') or 'Unknown'}\n"
        f"官网：{result.get('official_website') or 'Unknown'}\n"
        f"X：{result.get('official_x') or 'Unknown'}\n"
        f"平台代币：{token}\n"
        f"身份状态：{result.get('verification_status', 'NO_DATA')}\n"
        f"发现理由：{reason}\n"
        f"证据来源：{', '.join(sources) or '链上 Family'}"
    )
