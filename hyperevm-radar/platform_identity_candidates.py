import re
import requests

API_URL = "https://api.llama.fi/protocols"

TARGET_CATEGORIES = {
    "dexs",
    "dex",
    "lending",
    "yield",
    "yield aggregator",
    "liquid staking",
    "rwa",
    "derivatives",
    "options",
    "indexes",
    "risk curators",
    "onchain capital allocator",
}

REJECT_CATEGORIES = {
    "cex",
    "bridge",
    "nft marketplace",
    "gaming",
}


def norm(value):
    return re.sub(
        r"[^a-z0-9]+",
        "",
        str(value or "").lower(),
    )


def get_hyper_protocols():
    r = requests.get(
        API_URL,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    r.raise_for_status()

    out = []

    for p in r.json():
        chains = [
            str(x or "").lower()
            for x in (p.get("chains") or [])
        ]

        if not any(
            "hyperliquid" in x or "hyperevm" in x
            for x in chains
        ):
            continue

        category = str(
            p.get("category") or ""
        ).strip()

        if category.lower() in REJECT_CATEGORIES:
            continue

        out.append({
            "name": str(p.get("name") or "").strip(),
            "symbol": str(p.get("symbol") or "").strip(),
            "category": category,
            "url": str(p.get("url") or "").strip(),
            "slug": str(p.get("slug") or "").strip(),
            "chains": p.get("chains") or [],
        })

    return out


def score_candidate(family, protocol):
    """
    Discovery scoring only.

    Platform identity may be discovered from:
    - brand_hint
    - full on-chain token/product names
    - token symbols

    None of these confirm identity by themselves.
    """

    name = norm(protocol.get("name"))
    slug = norm(protocol.get("slug"))
    protocol_symbol = norm(protocol.get("symbol"))

    clues = []

    brand = str(
        family.get("brand_hint") or ""
    ).strip()

    if brand:
        clues.append(
            ("BRAND_HINT", brand, 100)
        )

    for token_name in family.get(
        "token_names", []
    ):
        token_name = str(
            token_name or ""
        ).strip()

        if token_name:
            clues.append(
                ("TOKEN_NAME", token_name, 85)
            )

    for token_symbol in family.get(
        "token_symbols", []
    ):
        token_symbol = str(
            token_symbol or ""
        ).strip()

        if token_symbol:
            clues.append(
                ("TOKEN_SYMBOL", token_symbol, 55)
            )

    best_score = 0
    best_reasons = []

    for clue_type, raw_clue, base in clues:
        clue = norm(raw_clue)

        if len(clue) < 3:
            continue

        score = 0
        reasons = []

        # Exact platform-name / slug match.
        if clue == name:
            score = base
            reasons.append(
                f"{clue_type}:EXACT_NAME"
            )

        if clue == slug:
            candidate_score = base

            if candidate_score > score:
                score = candidate_score

            reasons.append(
                f"{clue_type}:EXACT_SLUG"
            )

        # Full product names may contain the platform
        # brand plus product suffixes.
        #
        # Example pattern only:
        # PlatformName + Vault/Product/etc.
        #
        # Discovery only, never confirmation.
        if (
            len(name) >= 4
            and clue.startswith(name)
            and clue != name
        ):
            candidate_score = max(
                30,
                base - 40,
            )

            if candidate_score > score:
                score = candidate_score

            reasons.append(
                f"{clue_type}:NAME_IN_PRODUCT"
            )

        if (
            len(slug) >= 4
            and clue.startswith(slug)
            and clue != slug
        ):
            candidate_score = max(
                30,
                base - 40,
            )

            if candidate_score > score:
                score = candidate_score

            reasons.append(
                f"{clue_type}:SLUG_IN_PRODUCT"
            )

        # Symbol is weak discovery evidence.
        if (
            clue_type == "TOKEN_SYMBOL"
            and len(clue) >= 4
            and protocol_symbol
            and clue == protocol_symbol
        ):
            candidate_score = 45

            if candidate_score > score:
                score = candidate_score

            reasons.append(
                "TOKEN_SYMBOL:EXACT_SYMBOL"
            )

        if score > best_score:
            best_score = score
            best_reasons = reasons

    if best_score <= 0:
        return 0, []

    category = str(
        protocol.get("category") or ""
    ).lower()

    # Category can BOOST an existing identity clue.
    # It can NEVER create a candidate by itself.
    if category in TARGET_CATEGORIES:
        best_score += 10
        best_reasons.append(
            "TARGET_CATEGORY"
        )

    return best_score, best_reasons

def find_family_candidates(family, protocols, limit=5):
    ranked = []

    for p in protocols:
        score, reasons = score_candidate(
            family,
            p,
        )

        if score <= 0:
            continue

        item = dict(p)
        item["score"] = score
        item["reasons"] = reasons

        ranked.append(item)

    ranked.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    return ranked[:limit]


# =========================================================
# Community ecosystem identity discovery
#
# IMPORTANT:
# - Discovery only.
# - A name hit is NOT platform confirmation.
# - Resolver remains responsible for confirmation.
# =========================================================

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


ECOSYSTEM_SOURCES = [
    (
        "HYPURRCO",
        "https://www.hypurr.co/ecosystem-projects",
    ),
    (
        "HYPERLIQUID_WIKI",
        "https://hyperliquid.wiki/",
    ),
    (
        "HL_ECO",
        "https://hl.eco/projects",
    ),
]


class EcosystemPageParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.links = []
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return

        attrs = dict(attrs)
        href = attrs.get("href")

        if href:
            self.links.append(href)

    def handle_data(self, data):
        value = " ".join(
            str(data or "").split()
        )

        if value:
            self.text_parts.append(value)


def fetch_ecosystem_source(source_name, source_url):
    try:
        r = requests.get(
            source_url,
            timeout=30,
            headers={
                "User-Agent":
                    "Mozilla/5.0 HyperEVM-Platform-Radar/1.0"
            },
        )

        r.raise_for_status()

    except Exception as e:
        return {
            "source": source_name,
            "url": source_url,
            "ok": False,
            "error": repr(e),
            "text": "",
            "links": [],
        }

    parser = EcosystemPageParser()
    parser.feed(r.text)

    links = []

    for href in parser.links:

        try:
            absolute = urljoin(
                source_url,
                href,
            )
        except Exception:
            continue

        if not absolute.startswith(
            ("http://", "https://")
        ):
            continue

        if absolute not in links:
            links.append(absolute)

    return {
        "source": source_name,
        "url": source_url,
        "ok": True,
        "error": "",
        "text": " ".join(
            parser.text_parts
        ),
        "links": links,
    }


def ecosystem_family_match(
    family,
    source_page,
):
    """
    Find discovery evidence only.

    brand_hint and full token/product names are allowed
    to FIND a possible platform.

    They do NOT confirm identity.
    """

    if not source_page.get("ok"):
        return []

    page_text = str(
        source_page.get("text") or ""
    )

    normalized_page = norm(page_text)

    clues = []

    brand = str(
        family.get("brand_hint") or ""
    ).strip()

    if brand:
        clues.append(
            ("BRAND_HINT", brand)
        )

    for name in family.get(
        "token_names", []
    ):
        name = str(name or "").strip()

        if name:
            clues.append(
                ("TOKEN_NAME", name)
            )

    matches = []

    seen = set()

    for clue_type, clue in clues:

        n = norm(clue)

        if len(n) < 4:
            continue

        if n not in normalized_page:
            continue

        key = (
            clue_type,
            n,
            source_page["source"],
        )

        if key in seen:
            continue

        seen.add(key)

        matches.append({
            "source":
                source_page["source"],

            "source_url":
                source_page["url"],

            "clue_type":
                clue_type,

            "clue":
                clue,

            "status":
                "DISCOVERY_ONLY",
        })

    return matches


def discover_family_from_ecosystem(
    family,
):
    pages = [
        fetch_ecosystem_source(
            source_name,
            source_url,
        )
        for source_name, source_url
        in ECOSYSTEM_SOURCES
    ]

    matches = []

    for page in pages:
        matches.extend(
            ecosystem_family_match(
                family,
                page,
            )
        )

    return pages, matches


# =========================================================
# Unified Family Identity Discovery
#
# DISCOVERY ONLY.
#
# Responsibilities:
# - Accept any platform family.
# - Use all currently available identity sources.
# - Return possible platform identities.
#
# It MUST NOT:
# - confirm a platform
# - write database state
# - call Telegram
# - guess official token mappings
# =========================================================


def discover_family_identity(
    family,
    protocols=None,
):
    """
    Unified discovery entry point.

    Returns:
        {
            "family_id": ...,
            "candidates": [...],
            "sources": {...},
        }

    Candidate discovery != identity confirmation.
    """

    result = {
        "family_id": family.get("id"),
        "candidates": [],
        "sources": {},
    }

    seen = set()

    def add_candidate(item):
        if not item:
            return

        name = str(
            item.get("name") or ""
        ).strip()

        url = str(
            item.get("url") or ""
        ).strip()

        source = str(
            item.get("source") or ""
        ).strip()

        clue = str(
            item.get("clue") or ""
        ).strip()

        key = (
            name.lower(),
            url.lower(),
            source.lower(),
            clue.lower(),
        )

        if key in seen:
            return

        seen.add(key)
        result["candidates"].append(item)

    # -------------------------------------------------
    # 1. DefiLlama
    # -------------------------------------------------

    try:
        if protocols is None:
            protocols = get_hyper_protocols()

        matches = find_family_candidates(
            family,
            protocols,
            limit=10,
        )

        result["sources"]["DEFILLAMA"] = {
            "ok": True,
            "matches": len(matches),
        }

        for m in matches:
            add_candidate({
                "source": "DEFILLAMA",
                "name": m.get("name", ""),
                "url": m.get("url", ""),
                "symbol": m.get("symbol", ""),
                "category": m.get(
                    "category",
                    "",
                ),
                "chains": m.get(
                    "chains",
                    [],
                ),
                "score": m.get(
                    "score",
                    0,
                ),
                "reasons": m.get(
                    "reasons",
                    [],
                ),
                "status":
                    "DISCOVERY_ONLY",
            })

    except Exception as e:
        result["sources"]["DEFILLAMA"] = {
            "ok": False,
            "error": repr(e),
        }

    # -------------------------------------------------
    # 2. Ecosystem directories
    # -------------------------------------------------

    try:
        pages, matches = (
            discover_family_from_ecosystem(
                family
            )
        )

        result["sources"]["ECOSYSTEM"] = {
            "ok": True,
            "matches": len(matches),
            "pages": [
                {
                    "source":
                        p.get("source"),
                    "ok":
                        p.get("ok"),
                    "url":
                        p.get("url"),
                    "error":
                        p.get("error", ""),
                }
                for p in pages
            ],
        }

        for m in matches:
            add_candidate({
                "source":
                    m.get("source", ""),
                "name": "",
                "url":
                    m.get(
                        "source_url",
                        "",
                    ),
                "clue_type":
                    m.get(
                        "clue_type",
                        "",
                    ),
                "clue":
                    m.get(
                        "clue",
                        "",
                    ),
                "status":
                    "DISCOVERY_ONLY",
            })

    except Exception as e:
        result["sources"]["ECOSYSTEM"] = {
            "ok": False,
            "error": repr(e),
        }

    return result
