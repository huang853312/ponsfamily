import re
from collections import defaultdict

FAMILY_BLOCK_WINDOW = 5000

PRODUCT_WORDS = {
    "vault",
    "share",
    "shares",
    "token",
    "pool",
    "router",
    "factory",
    "bull",
    "bear",
    "vol",
    "ibull",
    "ibear",
    "ivol",
    "oracle",
    "registry",
    "hook",
    "amm",
    "dex",
    "swap",
    "lending",
    "lend",
    "borrow",
    "perp",
    "perpetual",
    "option",
    "options",
}


def brand_hint(name: str) -> str:
    name = (name or "").strip()

    if not name:
        return ""

    cleaned = re.sub(
        r"\b(" + "|".join(
            sorted(
                (re.escape(x) for x in PRODUCT_WORDS),
                key=len,
                reverse=True
            )
        ) + r")\b",
        " ",
        name,
        flags=re.I,
    )

    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return ""

    return cleaned.split()[0].lower()


def build_platform_families(candidate_rows, block_window=FAMILY_BLOCK_WINDOW):
    """
    Group candidates by:
      1. creator
      2. deployment-time proximity

    brand_hint is LABEL ONLY.
    It is never used as the primary grouping key.
    """

    grouped = defaultdict(list)

    for row in candidate_rows:
        creator = (row.get("creator") or "").lower()

        if not creator:
            continue

        grouped[creator].append(row)

    families = []

    for creator, items in grouped.items():
        items = sorted(
            items,
            key=lambda x: int(x.get("block_number", 0) or 0)
        )

        current = []
        last_block = None

        def flush():
            nonlocal current

            if not current:
                return

            hints = [
                brand_hint(x.get("token_name", ""))
                for x in current
            ]
            hints = [h for h in hints if h]

            hint_counts = {}

            for hint in hints:
                hint_counts[hint] = hint_counts.get(hint, 0) + 1

            best_hint = ""

            if hint_counts:
                best_hint = sorted(
                    hint_counts.items(),
                    key=lambda x: (-x[1], x[0])
                )[0][0]

            types = set()

            for x in current:
                raw = str(x.get("infra_roles", "") or "")

                for part in raw.split(","):
                    part = part.strip()

                    if part:
                        types.add(part)

            blocks = [
                int(x.get("block_number", 0) or 0)
                for x in current
            ]

            families.append({
                "creator": creator,
                "first_block": min(blocks),
                "last_block": max(blocks),
                "member_count": len(current),
                "platform_types": sorted(types),
                "brand_hint": best_hint,
                "members": [
                    {
                        "address": x.get("address", ""),
                        "block_number": int(
                            x.get("block_number", 0) or 0
                        ),
                    }
                    for x in current
                ],
            })

            current = []

        for row in items:
            block = int(row.get("block_number", 0) or 0)

            if (
                current
                and last_block is not None
                and block - last_block > block_window
            ):
                flush()

            current.append(row)
            last_block = block

        flush()

    return sorted(
        families,
        key=lambda x: (
            x["first_block"],
            x["creator"],
        )
    )
