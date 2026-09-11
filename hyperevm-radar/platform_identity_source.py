"""
Platform Identity Source V1

Purpose:
    Convert verified official-source facts into IdentityEvidence.

Important:
    - Does NOT guess websites from brand_hint.
    - Does NOT confirm from token/contract name alone.
    - Does NOT depend on Address Book.
    - Does NOT depend on Funding.
    - Does NOT modify A/B/S.
    - Does NOT send Telegram.

This module is deliberately evidence-first.
"""

from platform_identity_resolver import IdentityEvidence


def build_identity_evidence(
    family,
    *,
    project_name="",
    website_url="",
    official_token_name="",
    official_token_symbol="",
    website_mentions_hyperevm=False,
    website_mentions_chain_999=False,
    official_docs_found=False,
    candidate_address_on_official_site=False,
    creator_address_on_official_site=False,
    core_contract_on_official_site=False,
    source="",
):
    family = family or {}

    raw_types = family.get("platform_types") or ""

    if isinstance(raw_types, str):
        platform_types = [
            x.strip().upper()
            for x in raw_types.split(",")
            if x.strip()
        ]
    else:
        platform_types = [
            str(x).strip().upper()
            for x in raw_types
            if str(x).strip()
        ]

    # brand_hint is only a hint for investigation.
    # It must never become a confirmed project name by itself.
    project_name = str(project_name or "").strip()
    website_url = str(website_url or "").strip()

    return IdentityEvidence(
        project_name=project_name,
        website_url=website_url,
        platform_types=platform_types,
        official_token_name=str(
            official_token_name or ""
        ).strip(),
        official_token_symbol=str(
            official_token_symbol or ""
        ).strip(),
        website_mentions_hyperevm=bool(
            website_mentions_hyperevm
        ),
        website_mentions_chain_999=bool(
            website_mentions_chain_999
        ),
        official_docs_found=bool(
            official_docs_found
        ),
        candidate_address_on_official_site=bool(
            candidate_address_on_official_site
        ),
        creator_address_on_official_site=bool(
            creator_address_on_official_site
        ),
        core_contract_on_official_site=bool(
            core_contract_on_official_site
        ),
        source=str(source or "").strip(),
    )


# =========================================================
# Candidate Website Verification
#
# Takes a discovered candidate URL and extracts evidence.
# Discovery != confirmation.
# Resolver remains the only confirmation authority.
# =========================================================

import re
import requests
from urllib.parse import urlparse


TARGET_TEXT_WORDS = {
    "launchpad",
    "launcher",
    "dex",
    "amm",
    "router",
    "swap",
    "liquidity",
    "lending",
    "borrow",
    "vault",
    "yield",
    "oracle",
    "rwa",
    "stock",
    "equity",
    "settlement",
    "clearing",
    "perp",
    "perpetual",
    "options",
    "derivatives",
}


def _normalize_page_text(value):
    return " ".join(
        str(value or "").lower().split()
    )


def verify_candidate_website(
    family,
    candidate,
):
    """
    Verify a discovered candidate website.

    Returns IdentityEvidence.

    Important:
    - Does NOT confirm identity itself.
    - Does NOT write DB.
    - Does NOT infer official token aliases.
    """

    url = str(
        candidate.get("url") or ""
    ).strip()

    project_name = str(
        candidate.get("name") or ""
    ).strip()

    source = str(
        candidate.get("source") or ""
    ).strip()

    if not url:
        return build_identity_evidence(
            family,
            project_name=project_name,
            website_url="",
            source=source,
        )

    try:
        parsed = urlparse(url)

        if parsed.scheme not in (
            "http",
            "https",
        ):
            raise ValueError(
                "unsupported URL scheme"
            )

        r = requests.get(
            url,
            timeout=20,
            allow_redirects=True,
            headers={
                "User-Agent":
                    "Mozilla/5.0 HyperEVM-Platform-Radar/1.0"
            },
        )

        r.raise_for_status()

        final_url = str(r.url or url)

        text = _normalize_page_text(
            r.text
        )

    except Exception:
        return build_identity_evidence(
            family,
            project_name=project_name,
            website_url=url,
            source=source,
        )

    # Chain evidence.
    mentions_hyperevm = (
        "hyperevm" in text
        or "hyper evm" in text
    )

    mentions_chain_999 = bool(
        re.search(
            r"\bchain\s*(?:id)?\s*[:#=-]?\s*999\b",
            text,
        )
    )

    # Target-finance evidence.
    found_types = []

    for word in TARGET_TEXT_WORDS:
        if re.search(
            r"\b" + re.escape(word) + r"\b",
            text,
        ):
            found_types.append(
                word.upper()
            )

    # Preserve the Family's existing on-chain
    # platform type evidence.
    evidence_family = dict(family)

    existing_types = str(
        family.get("platform_types") or ""
    ).strip()

    combined = []

    if existing_types:
        combined.extend(
            x.strip()
            for x in existing_types.split(",")
            if x.strip()
        )

    combined.extend(found_types)

    evidence_family["platform_types"] = ",".join(
        dict.fromkeys(combined)
    )

    return build_identity_evidence(
        evidence_family,
        project_name=project_name,
        website_url=final_url,
        website_mentions_hyperevm=mentions_hyperevm,
        website_mentions_chain_999=mentions_chain_999,
        source=source,
    )
