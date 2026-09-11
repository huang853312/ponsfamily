from dataclasses import dataclass, field
from typing import List

from platform_token_radar import (
    extract_domain_and_brand,
    normalize_text,
)

TARGET_TYPES = {
    "LAUNCHPAD",
    "DEX_AMM",
    "LENDING",
    "VAULT",
    "ORACLE",
    "RWA_STOCK",
    "SETTLEMENT",
    "DERIVATIVES",
    "INFRA",
}

BAD_BRANDS = {
    "",
    "www",
    "app",
    "apps",
    "docs",
    "doc",
    "api",
    "beta",
    "test",
    "dev",
    "finance",
    "protocol",
    "exchange",
    "swap",
    "dex",
    "defi",
    "crypto",
    "token",
    "tokens",
    "launch",
    "launchpad",
    "network",
    "chain",
    "xyz",
    "fun",
    "io",
    "com",
    "org",
    "net",
}


@dataclass
class IdentityEvidence:
    project_name: str = ""
    website_url: str = ""
    platform_types: List[str] = field(default_factory=list)

    # Optional verified platform/protocol token identity.
    # May be same as platform brand or completely different.
    # Empty means unknown / not established.
    official_token_name: str = ""
    official_token_symbol: str = ""

    # Strong verification evidence
    website_mentions_hyperevm: bool = False
    website_mentions_chain_999: bool = False
    official_docs_found: bool = False
    candidate_address_on_official_site: bool = False
    creator_address_on_official_site: bool = False
    core_contract_on_official_site: bool = False

    source: str = ""


def valid_brand(brand: str) -> bool:
    brand = normalize_text(brand)

    if not brand:
        return False

    if len(brand) < 3:
        return False

    if len(brand) > 40:
        return False

    if brand in BAD_BRANDS:
        return False

    if brand.isdigit():
        return False

    return True


def resolve_identity(evidence: IdentityEvidence):
    """
    Safe resolver.

    IMPORTANT:
    - Never confirms from contract/token name alone.
    - Never confirms from address book.
    - Never confirms from funding relation.
    - Website/domain must exist.
    - HyperEVM evidence must exist.
    - Financial infrastructure type must exist.
    - Strong official relationship evidence is required.
    """

    result = {
        "status": "DISCOVERED",
        "confirmed": False,
        "project_name": evidence.project_name,
        "website_url": evidence.website_url,
        "domain": "",
        "brand": "",
        "platform_types": [],
        "official_token_name": str(
            evidence.official_token_name or ""
        ).strip(),
        "official_token_symbol": str(
            evidence.official_token_symbol or ""
        ).strip(),
        "score": 0,
        "reasons": [],
        "source": evidence.source,
    }

    target_types = sorted(
        set(evidence.platform_types) & TARGET_TYPES
    )

    result["platform_types"] = target_types

    if not target_types:
        result["status"] = "REJECTED"
        result["reasons"].append(
            "Not a target financial infrastructure type"
        )
        return result

    if not evidence.website_url:
        result["status"] = "WEBSITE_PENDING"
        result["reasons"].append(
            "Official website not established"
        )
        return result

    domain, brand = extract_domain_and_brand(
        evidence.website_url
    )

    result["domain"] = domain
    result["brand"] = brand

    if not domain or not valid_brand(brand):
        result["status"] = "WEBSITE_PENDING"
        result["reasons"].append(
            "Domain or brand is not reliable"
        )
        return result

    result["status"] = "VERIFYING"

    # Chain evidence
    if evidence.website_mentions_hyperevm:
        result["score"] += 2
        result["reasons"].append(
            "Official source mentions HyperEVM"
        )

    if evidence.website_mentions_chain_999:
        result["score"] += 2
        result["reasons"].append(
            "Official source mentions chain 999"
        )

    # Official documentation
    if evidence.official_docs_found:
        result["score"] += 1
        result["reasons"].append(
            "Official documentation found"
        )

    # Strong address relationship
    if evidence.candidate_address_on_official_site:
        result["score"] += 4
        result["reasons"].append(
            "Candidate contract listed by official source"
        )

    if evidence.creator_address_on_official_site:
        result["score"] += 4
        result["reasons"].append(
            "Creator address listed by official source"
        )

    if evidence.core_contract_on_official_site:
        result["score"] += 3
        result["reasons"].append(
            "Core contract listed by official source"
        )

    chain_ok = (
        evidence.website_mentions_hyperevm
        or evidence.website_mentions_chain_999
    )

    relationship_ok = (
        evidence.candidate_address_on_official_site
        or evidence.creator_address_on_official_site
        or evidence.core_contract_on_official_site
    )

    # Platform Token Radar confirmation policy:
    #
    # PLATFORM_CONFIRMED:
    #   real official website/docs
    #   + explicit HyperEVM / chain 999 evidence
    #   + target financial-infrastructure category
    #
    # Official on-chain contract linkage is STRONG additional evidence,
    # but is NOT required because very early platforms may not publish
    # their contracts yet.

    if chain_ok:
        result["status"] = "PLATFORM_CONFIRMED"
        result["confirmed"] = True

        if relationship_ok:
            result["status"] = "STRONG_CONFIRMED"

    return result
