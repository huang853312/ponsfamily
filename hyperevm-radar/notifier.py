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
