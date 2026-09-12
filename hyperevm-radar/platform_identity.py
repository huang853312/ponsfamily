import re

from platform_token_radar import classify_platform_text


GENERIC_CONTRACT_NAMES = {
    "proxy",
    "erc1967proxy",
    "transparentupgradeableproxy",
    "implementation",
    "router",
    "factory",
    "vault",
    "oracle",
    "pool",
    "token",
    "registry",
    "hook",
    "aggregator",
}


def normalize_contract_name(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"[^A-Za-z0-9_\- ]", "", value)
    return value[:200]


def analyze_verified_identity(
    contract_name="",
    source_code="",
    abi_text="",
):
    """
    Analyze explorer verified-contract metadata.

    IMPORTANT:
    - Does NOT use address_book.
    - Does NOT invent a website.
    - Does NOT confirm a platform by itself.
    """

    contract_name = normalize_contract_name(contract_name)

    classification = classify_platform_text(
        contract_name,
        source_code[:100000],
        abi_text[:30000],
    )

    compact_name = re.sub(
        r"[^a-z0-9]",
        "",
        contract_name.lower(),
    )

    generic_name = (
        not compact_name
        or compact_name in {
            re.sub(r"[^a-z0-9]", "", x)
            for x in GENERIC_CONTRACT_NAMES
        }
    )

    return {
        "contract_name": contract_name,
        "eligible": classification["eligible"],
        "platform_types": classification.get("types", []),
        "matched_keywords": classification.get(
            "matched_keywords", []
        ),
        "generic_contract_name": generic_name,

        # Website confirmation is deliberately separate.
        "website_confirmed": False,
        "website_url": "",
        "domain": "",
        "brand": "",
    }
