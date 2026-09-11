import re
from urllib.parse import urlparse


COMMON_SUBDOMAINS = {
    "www", "app", "docs", "doc", "beta", "test", "dev",
    "portal", "dashboard", "trade", "swap", "api"
}


def normalize_text(value: str) -> str:
    if not value:
        return ""

    value = value.strip().lower()

    # Keep letters/numbers only.
    return re.sub(r"[^a-z0-9]", "", value)


def extract_domain_and_brand(url: str):
    """
    Dynamically extract a platform domain + brand candidate.

    Examples:
        https://pair.fund       -> pair.fund / pair
        https://www.alpha.xyz   -> alpha.xyz / alpha
        https://app.moon.fun    -> moon.fun / moon

    PAIR is NOT hard-coded.
    """
    if not url:
        return "", ""

    raw = url.strip()

    if "://" not in raw:
        raw = "https://" + raw

    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower().strip(".")
    except Exception:
        return "", ""

    if not host:
        return "", ""

    parts = [p for p in host.split(".") if p]

    while len(parts) > 2 and parts[0] in COMMON_SUBDOMAINS:
        parts.pop(0)

    if len(parts) < 2:
        return host, normalize_text(parts[0] if parts else "")

    # First version:
    # brand = label immediately before TLD.
    brand = parts[-2]
    domain = ".".join(parts[-2:])

    return domain, normalize_text(brand)


def token_matches_brand(token_name: str, token_symbol: str, brand: str):
    """
    Conservative V1 matching:
    exact normalized name OR exact normalized symbol only.
    No fuzzy matching to avoid Telegram spam.
    """
    brand_norm = normalize_text(brand)

    if not brand_norm:
        return None

    name_norm = normalize_text(token_name)
    symbol_norm = normalize_text(token_symbol)

    if name_norm and name_norm == brand_norm:
        return "EXACT_NAME"

    if symbol_norm and symbol_norm == brand_norm:
        return "EXACT_SYMBOL"

    return None


# =========================================================
# Target platform classification
#
# Only these directions are eligible for Platform -> Token.
# Ordinary meme/NFT/game/token marketing sites are excluded.
# =========================================================

TARGET_PLATFORM_KEYWORDS = {
    "LAUNCHPAD": {
        "launchpad", "launcher", "launch", "bondingcurve",
        "bonding curve", "tokenfactory", "token factory"
    },

    "DEX_AMM": {
        "dex", "amm", "router", "swap", "pool",
        "liquidity", "aggregator"
    },

    "LENDING": {
        "lend", "lending", "borrow", "borrowing",
        "money market"
    },

    "VAULT": {
        "vault", "yield", "strategy", "allocator"
    },

    "ORACLE": {
        "oracle", "pricefeed", "price feed"
    },

    "RWA_STOCK": {
        "rwa", "stock", "stocks", "equity", "equities",
        "share", "shares", "tokenized stock",
        "tokenized equity", "real world asset"
    },

    "SETTLEMENT": {
        "settlement", "clearing", "clearinghouse"
    },

    "DERIVATIVES": {
        "perp", "perpetual", "perpetuals",
        "option", "options", "derivative", "derivatives"
    },

    "INFRA": {
        "factory", "registry", "hook",
        "liquidity infrastructure",
        "financial infrastructure"
    },
}


EXCLUDED_PLATFORM_KEYWORDS = {
    "meme",
    "memecoin",
    "nft",
    "game",
    "gaming",
    "casino",
    "lottery",
    "social",
    "blog",
    "news",
    "faucet",
}


def classify_platform_text(*values):
    """
    Conservative classification.

    Returns:
        {
            "eligible": bool,
            "types": [...],
            "matched_keywords": [...]
        }
    """
    text = " ".join(
        str(v or "").lower()
        for v in values
    )

    if not text.strip():
        return {
            "eligible": False,
            "types": [],
            "matched_keywords": [],
        }

    excluded = sorted(
        kw for kw in EXCLUDED_PLATFORM_KEYWORDS
        if kw in text
    )

    if excluded:
        return {
            "eligible": False,
            "types": [],
            "matched_keywords": [],
            "excluded_keywords": excluded,
        }

    types = []
    hits = []

    for platform_type, keywords in TARGET_PLATFORM_KEYWORDS.items():
        matched = sorted(
            kw for kw in keywords
            if kw in text
        )

        if matched:
            types.append(platform_type)
            hits.extend(matched)

    return {
        "eligible": bool(types),
        "types": sorted(set(types)),
        "matched_keywords": sorted(set(hits)),
    }


# =========================================================
# Independent on-chain Platform Discovery
#
# IMPORTANT:
# - Does NOT change old A/B/S grading.
# - Does NOT depend on address_book.
# - Does NOT depend on Funding Radar.
# - Candidate only. Never confirms a platform by itself.
# =========================================================

ONCHAIN_PLATFORM_WORDS = {
    "LAUNCHPAD": {
        "launchpad", "launcher", "launch",
        "bondingcurve", "bonding", "tokenfactory",
    },
    "DEX_AMM": {
        "dex", "amm", "router", "swap",
        "pool", "liquidity", "aggregator",
    },
    "LENDING": {
        "lend", "lending", "borrow", "borrowing",
    },
    "VAULT": {
        "vault", "yield", "strategy", "allocator",
    },
    "ORACLE": {
        "oracle", "pricefeed",
    },
    "RWA_STOCK": {
        "rwa", "stock", "stocks", "equity",
        "equities", "share", "shares",
    },
    "SETTLEMENT": {
        "settlement", "clearing", "clearinghouse",
    },
    "DERIVATIVES": {
        "perp", "perpetual", "perpetuals",
        "option", "options", "derivative", "derivatives",
    },
    "INFRA": {
        "factory", "registry", "hook",
    },
}


def classify_platform_words(words):
    """
    Independent Platform Discovery classifier.

    Input:
        words extracted from deployed bytecode.

    Output:
        eligible
        types
        matched_keywords

    This is candidate discovery only.
    It does NOT mean the contract is a confirmed platform.
    """

    words = {
        str(w).strip().lower()
        for w in (words or set())
        if str(w).strip()
    }

    types = []
    hits = []

    for platform_type, keywords in ONCHAIN_PLATFORM_WORDS.items():
        matched = sorted(words & keywords)

        if matched:
            types.append(platform_type)
            hits.extend(matched)

    return {
        "eligible": bool(types),
        "types": sorted(set(types)),
        "matched_keywords": sorted(set(hits)),
    }
