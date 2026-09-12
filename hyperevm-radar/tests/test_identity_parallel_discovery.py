import unittest

from platform_family_intelligence import (
    FamilyIntelligenceEngine,
    Page,
    PageProvider,
    SearchHit,
    SearchProvider,
    XContentProvider,
)

CREATOR = "0x" + "11" * 20
MEMBER = "0x" + "22" * 20


def family():
    return {
        "id": 91,
        "creator": CREATOR,
        "member_addresses": [MEMBER],
        "member_count": 2,
        "platform_types": "ROUTER,VAULT",
        "brand_hint": "",
        "token_names": [],
        "token_symbols": [],
    }


class QuerySearch(SearchProvider):
    def __init__(self, resolver):
        self.resolver = resolver
        self.queries = []

    def search(self, query, timeout):
        self.queries.append(query)
        return list(self.resolver(query))


class Pages(PageProvider, XContentProvider):
    def __init__(self, values):
        self.values = values

    def fetch(self, url, timeout):
        return self.values.get(url, Page(url, status="NO_DATA"))


class ParallelIdentityDiscoveryTests(unittest.TestCase):
    def test_address_can_find_docs_first_then_backfill_site_and_x(self):
        docs = "https://docs.fastrouter.dev/contracts"
        site = "https://fastrouter.dev"
        x = "https://x.com/fastrouter"

        def resolve(query):
            if "(docs OR documentation OR contracts)" in query and MEMBER in query:
                return [SearchHit(docs, source="docs-search")]
            return []

        pages = Pages({
            docs: Page(docs, text=f"HyperEVM router deployment {MEMBER}", links=[site, x], title="FastRouter Docs", status="AVAILABLE"),
            site: Page(site, text="FastRouter HyperEVM router", links=[x, docs], title="FastRouter", status="AVAILABLE"),
            x: Page(x, text="FastRouter on HyperEVM", links=[site], title="FastRouter", status="AVAILABLE"),
        })
        search = QuerySearch(resolve)
        result = FamilyIntelligenceEngine(search=search, pages=pages, x_provider=pages).enrich(family())

        self.assertIn(docs, result["docs"])
        self.assertEqual(result["official_website"], site)
        self.assertEqual(result["official_x"], x)
        self.assertEqual(result["verification_status"], "VERIFIED")
        self.assertTrue(any("site:x.com" in query and MEMBER in query for query in search.queries))
        self.assertTrue(any("docs OR documentation OR contracts" in query and MEMBER in query for query in search.queries))

    def test_x_can_be_found_before_website_and_backfill_homepage(self):
        site = "https://xfirst.dev"
        x = "https://x.com/xfirst"

        def resolve(query):
            if query.startswith("site:x.com") and MEMBER in query:
                return [SearchHit(x, source="x-search")]
            return []

        pages = Pages({
            x: Page(x, text="XFirst HyperEVM vault", links=[site], title="XFirst", status="AVAILABLE"),
            site: Page(site, text=f"XFirst vault {MEMBER}", links=[x], title="XFirst", status="AVAILABLE"),
        })
        result = FamilyIntelligenceEngine(search=QuerySearch(resolve), pages=pages, x_provider=pages).enrich(family())

        self.assertEqual(result["official_x"], x)
        self.assertEqual(result["official_website"], site)
        self.assertEqual(result["verification_status"], "VERIFIED")

    def test_parked_domain_is_not_kept_as_project_website(self):
        parked = "https://parked.example"
        search = QuerySearch(lambda query: [SearchHit(parked, source="search")] if MEMBER in query else [])
        pages = Pages({
            parked: Page(parked, text="This domain is for sale. Buy this domain today.", title="Domain for sale", status="AVAILABLE")
        })
        result = FamilyIntelligenceEngine(search=search, pages=pages, x_provider=pages).enrich(family())

        self.assertEqual(result["official_website"], "")
        self.assertEqual(result["verification_status"], "NO_DATA")
        self.assertTrue(any(item.get("status") == "REJECTED_PARKED" for item in result["evidence"]))


if __name__ == "__main__":
    unittest.main()
