import unittest

from platform_family_intelligence import FamilyIntelligenceEngine, Page, PageProvider, SearchHit, SearchProvider, XContentProvider

CREATOR = "0x" + "11" * 20
MEMBER1 = "0x" + "22" * 20
MEMBER2 = "0x" + "33" * 20
MEMBER3 = "0x" + "44" * 20

class Search(SearchProvider):
    def __init__(self, resolver): self.resolver=resolver; self.queries=[]
    def search(self, query, timeout): self.queries.append(query); return list(self.resolver(query))

class Pages(PageProvider, XContentProvider):
    def __init__(self, values): self.values=values
    def fetch(self, url, timeout): return self.values.get(url, Page(url,status="NO_DATA"))

def fam():
    return {"id":301,"creator":CREATOR,"member_addresses":[MEMBER1,MEMBER2,MEMBER3],"member_count":4,"platform_types":"ROUTER,VAULT","brand_hint":"","token_names":[],"token_symbols":[]}

class DocsIdentityDiscoveryTests(unittest.TestCase):
    def test_searches_creator_and_later_family_members_for_docs_and_crawls_deployment_page(self):
        docs="https://docs.deepinfra.dev"; deploy=docs+"/contracts/deployments"; site="https://deepinfra.dev"; x="https://x.com/deepinfra"
        def resolver(query):
            if MEMBER3 in query and "deployments" in query: return [SearchHit(docs,source="docs-search")]
            return []
        pages=Pages({
            docs:Page(docs,text="DeepInfra docs",links=[deploy,site,x],title="DeepInfra Docs",status="AVAILABLE"),
            deploy:Page(deploy,text=f"HyperEVM Router {MEMBER3}",links=[site,x],title="Deployments",status="AVAILABLE"),
            site:Page(site,text="DeepInfra HyperEVM router",links=[x,docs],title="DeepInfra",status="AVAILABLE"),
            x:Page(x,text="DeepInfra HyperEVM",links=[site],title="DeepInfra",status="AVAILABLE"),
        })
        search=Search(resolver)
        result=FamilyIntelligenceEngine(search=search,pages=pages,x_provider=pages,total_timeout=90).enrich(fam())
        self.assertEqual(result["verification_status"],"VERIFIED")
        self.assertEqual(result["official_website"],site)
        self.assertIn(deploy,result["docs"])
        self.assertTrue(any(MEMBER3 in q and "deployments" in q for q in search.queries))
        self.assertTrue(any(item.get("docs_crosslinked") for item in result["evidence"] if item.get("kind")=="CROSS_VERIFICATION"))

    def test_orphan_docs_address_alone_does_not_verify(self):
        docs="https://lonely.gitbook.io/project/contracts"
        def resolver(query):
            if CREATOR in query and "deployments" in query: return [SearchHit(docs,source="docs-search")]
            return []
        pages=Pages({docs:Page(docs,text=f"HyperEVM contract {CREATOR}",links=[],title="Lonely Docs",status="AVAILABLE")})
        result=FamilyIntelligenceEngine(search=Search(resolver),pages=pages,x_provider=pages,total_timeout=90).enrich(fam())
        self.assertNotEqual(result["verification_status"],"VERIFIED")
        cross=next(x for x in result["evidence"] if x.get("kind")=="CROSS_VERIFICATION")
        self.assertTrue(cross["family_address_in_docs"])
        self.assertFalse(cross["docs_crosslinked"])

# Deployment-triggering test file: behavior above guards the strengthened Docs path.
if __name__ == "__main__": unittest.main()
