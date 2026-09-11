from database import save_platform_brand
from platform_token_radar import normalize_text


CONFIRMED_STATUSES = {
    "PLATFORM_CONFIRMED",
    "STRONG_CONFIRMED",
}


def promote_identity_to_brand(
    result,
    *,
    first_seen_block,
    platform_address="",
):
    """
    Promote a confirmed platform identity into platform_brands.

    Does NOT depend on:
    - address_book
    - Funding Radar
    - A/B/S grading

    Requires:
    - confirmed identity result
    - valid brand/domain
    - known platform first_seen_block > 0
    """

    if not result:
        return {
            "saved": False,
            "reason": "empty result",
        }

    status = str(result.get("status") or "").strip()

    if status not in CONFIRMED_STATUSES:
        return {
            "saved": False,
            "reason": f"status not confirmed: {status}",
        }

    if not result.get("confirmed"):
        return {
            "saved": False,
            "reason": "confirmed flag is false",
        }

    brand = normalize_text(
        result.get("brand") or ""
    )

    domain = str(
        result.get("domain") or ""
    ).strip().lower()

    website_url = str(
        result.get("website_url") or ""
    ).strip()

    project_name = str(
        result.get("project_name") or brand
    ).strip()

    platform_types = result.get(
        "platform_types"
    ) or []

    official_token_name = str(
        result.get("official_token_name") or ""
    ).strip()

    official_token_symbol = str(
        result.get("official_token_symbol") or ""
    ).strip()

    first_seen_block = int(
        first_seen_block or 0
    )

    if not brand:
        return {
            "saved": False,
            "reason": "missing brand",
        }

    if not domain:
        return {
            "saved": False,
            "reason": "missing domain",
        }

    if first_seen_block <= 0:
        return {
            "saved": False,
            "reason": "missing reliable first_seen_block",
        }

    if isinstance(platform_types, (list, tuple, set)):
        platform_type = ",".join(
            sorted(set(str(x) for x in platform_types if x))
        )
    else:
        platform_type = str(platform_types or "")

    saved = save_platform_brand(
        platform_name=project_name,
        normalized_brand=brand,
        domain=domain,
        website_url=website_url,
        platform_type=platform_type,
        platform_address=platform_address,
        first_seen_block=first_seen_block,
        official_token_name=official_token_name,
        official_token_symbol=official_token_symbol,
        confidence=status,
        confirmed=True,
        source=(
            "IDENTITY_RESOLVER:"
            + status
        ),
    )

    return {
        "saved": bool(saved),
        "reason": (
            "promoted"
            if saved
            else "database save failed"
        ),
        "brand": brand,
        "domain": domain,
        "status": status,
        "first_seen_block": first_seen_block,
    }
