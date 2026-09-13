import unittest

from platform_family_intelligence import (
    FamilyIntelligenceEngine, Page, PageProvider, SearchHit, SearchProvider,
    XContentProvider, extract_core_word,
)

CREATOR = "0x" + "aa" * 20


class Search(SearchProvider):
    def search(self, query, timeout):
        if CREATOR in query and "docs" in query.lower():
            return [SearchHit("https://fakeproject.gitbook.io/docs/contracts", source="docs-search")]
        return []


class Pages(PageProvider, XContentProvider):
    def fetch(self, url, timeout):
        pages = {
            "https://fakeproject.gitbook.io/docs/contracts": Page(
                "https://fakeproject.gitbook.io/docs/contracts",
                text=f"HyperEVM contract {CREATOR}",
                links=["https://www.gitbook.com/", "https://x.com/GitBookIO"],
                title="Project Docs",
                status="AVAILABLE",
            ),
            "https://www.gitbook.com/": Page(
                "https://www.gitbook.com/",
                text="The knowledge layer for AI",
                links=["https://x.com/GitBookIO"],
                title="GitBook - The knowledge layer for AI",
                description="The knowledge layer for AI",
                status="AVAILABLE",
            ),
            "https://x.com/GitBookIO": Page(
                "https://x.com/GitBookIO",
                text="GitBook",
                links=["https://www.gitbook.com/"],
                title="GitBook",
                status="AVAILABLE",
            ),
        }
        return pages.get(url, Page(url, status="NO_DATA"))


def family():
    return {
        "id": 909,
        "creator": CREATOR,
        "member_addresses": [],
        "member_count": 1,
        "platform_types": "DEX_AMM,RWA_STOCK",
        "brand_hint": "",
        "token_names": [],
        "token_symbols": [],
    }


class GitBookIdentityGuardTests(unittest.TestCase):
    def test_gitbook_provider_identity_cannot_verify_family(self):
        result = FamilyIntelligenceEngine(
            search=Search(), pages=Pages(), x_provider=Pages(), total_timeout=90
        ).enrich(family())
        self.assertNotEqual(result["verification_status"], "VERIFIED")
        self.assertNotEqual(result.get("official_website"), "https://www.gitbook.com/")
        self.assertNotEqual(result.get("official_x"), "https://x.com/GitBookIO")

    def test_gitbook_shared_host_never_becomes_core_word(self):
        self.assertEqual(extract_core_word("https://www.gitbook.com/"), "")
        self.assertEqual(extract_core_word("https://project.gitbook.io/docs"), "")


if __name__ == "__main__":
    unittest.main()
