import unittest
from unittest.mock import patch

from platform_family_intelligence import FamilyIntelligenceEngine, Page, PageProvider, SearchHit, SearchProvider, XContentProvider

CREATOR="0x"+"11"*20
M1="0x"+"22"*20
M2="0x"+"33"*20
M3="0x"+"44"*20
TOKEN="0x"+"55"*20

class Search(SearchProvider):
    def __init__(self,resolver=None): self.resolver=resolver or (lambda q: []); self.queries=[]
    def search(self,q,timeout): self.queries.append(q); return list(self.resolver(q))

class Pages(PageProvider,XContentProvider):
    def __init__(self,values): self.values=values
    def fetch(self,url,timeout): return self.values.get(url,Page(url,status="NO_DATA"))

def fam():
    return {"id":501,"creator":CREATOR,"member_addresses":[M1,M2,M3],"member_count":4,"platform_types":"ROUTER,VAULT","brand_hint":"","token_names":[],"token_symbols":[]}

class IdentityTokenStrongTests(unittest.TestCase):
    def test_later_family_member_direct_metadata_can_recover_identity(self):
        site="https://laterfam.dev"; x="https://x.com/laterfam"
        pages=Pages({site:Page(site,text=f"LaterFam HyperEVM router {M3}",links=[x],title="LaterFam",status="AVAILABLE"),x:Page(x,text="LaterFam HyperEVM",links=[site],title="LaterFam",status="AVAILABLE")})
        class Response:
            def raise_for_status(self): pass
            def json(self): return [{"info":{"websites":[{"url":site}],"socials":[{"platform":"twitter","handle":"laterfam"}]},"baseToken":{"address":M3,"name":"LaterFam","symbol":"LATE"}}]
        def fake_get(url,*args,**kwargs):
            if M3 in url: return Response()
            raise RuntimeError("no metadata")
        with patch("platform_family_intelligence.requests.get",side_effect=fake_get):
            result=FamilyIntelligenceEngine(search=Search(),pages=pages,x_provider=pages,total_timeout=90).enrich(fam())
        self.assertEqual(result["official_website"],site)
        self.assertEqual(result["verification_status"],"VERIFIED")

    def test_official_linked_token_page_finds_ca_without_search_index(self):
        site="https://novagrid.dev"; token_page=site+"/token"; x="https://x.com/novagrid"
        pages=Pages({site:Page(site,text=f"NovaGrid HyperEVM router {M1}",links=[x,token_page],title="NovaGrid",status="AVAILABLE"),x:Page(x,text="NovaGrid HyperEVM",links=[site],title="NovaGrid",status="AVAILABLE"),token_page:Page(token_page,text=f"Official NovaGrid governance token NOV contract address {TOKEN}",title="NovaGrid Token",status="AVAILABLE")})
        result=FamilyIntelligenceEngine(search=Search(lambda q:[SearchHit(site)] if M1 in q else []),pages=pages,x_provider=pages,total_timeout=90).enrich(fam())
        self.assertEqual(result["derived_words"],["nov","nova","novag"])
        self.assertEqual(result["official_token_ca"],TOKEN)
        self.assertEqual(result["token_status"],"CONFIRMED_OFFICIAL")

    def test_dex_candidate_needs_exact_official_reverse_match(self):
        site="https://novagrid.dev"; x="https://x.com/novagrid"
        pages=Pages({site:Page(site,text=f"NovaGrid HyperEVM router {M1}. Official token address {TOKEN}",links=[x],title="NovaGrid",status="AVAILABLE"),x:Page(x,text="NovaGrid HyperEVM",links=[site],title="NovaGrid",status="AVAILABLE")})
        class Response:
            def __init__(self,payload): self.payload=payload
            def raise_for_status(self): pass
            def json(self): return self.payload
        def fake_get(url,*args,**kwargs):
            if "latest/dex/search" in url: return Response({"pairs":[{"chainId":"hyperevm","url":"https://dexscreener.com/hyperevm/pair","pairAddress":"0x"+"66"*20,"baseToken":{"address":TOKEN,"name":"NovaGrid","symbol":"NOV"},"quoteToken":{"address":"0x"+"77"*20,"name":"WHYPE","symbol":"WHYPE"}}]})
            raise RuntimeError("no direct metadata")
        with patch("platform_family_intelligence.requests.get",side_effect=fake_get):
            result=FamilyIntelligenceEngine(search=Search(lambda q:[SearchHit(site)] if M1 in q else []),pages=pages,x_provider=pages,total_timeout=90).enrich(fam())
        self.assertEqual(result["official_token_ca"],TOKEN)
        self.assertEqual(result["token_status"],"CONFIRMED_OFFICIAL")

    def test_three_four_five_search_rule_is_preserved(self):
        site="https://novagrid.dev"
        search=Search(lambda q:[SearchHit(site)] if M1 in q else [])
        pages=Pages({site:Page(site,text=f"NovaGrid HyperEVM router {M1}",title="NovaGrid",status="AVAILABLE")})
        FamilyIntelligenceEngine(search=search,pages=pages,x_provider=pages,total_timeout=90).enrich(fam())
        joined="\n".join(search.queries)
        for clue in ("novagrid","nov","nova","novag"):
            self.assertIn(f'"{clue}"',joined)

if __name__=="__main__": unittest.main()
