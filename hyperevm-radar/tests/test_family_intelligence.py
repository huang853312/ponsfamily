import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import main
from main import request_identity_refresh
from platform_family_intelligence import (
    FamilyIntelligenceEngine, Page, PageProvider, PublicPageProvider, SearchHit, SearchProvider, XContentProvider,
    derive_core_words, discover_official_token, domain_parts, extract_core_word,
)


CREATOR = "0x" + "11" * 20
MEMBER = "0x" + "22" * 20
TOKEN = "0x" + "33" * 20


def family(**updates):
    value = {"id": 7, "creator": CREATOR, "member_addresses": [MEMBER], "member_count": 2,
             "platform_types": "ROUTER,VAULT", "brand_hint": "", "token_names": [], "token_symbols": []}
    value.update(updates);return value


class Search(SearchProvider):
    def __init__(self,hits=(),error=None):self.hits=list(hits);self.error=error;self.queries=[]
    def search(self,query,timeout):
        self.queries.append(query)
        if self.error:raise self.error
        return self.hits


class Pages(PageProvider, XContentProvider):
    def __init__(self,values=None,error=None):self.values=values or {};self.error=error
    def fetch(self,url,timeout):
        if self.error:raise self.error
        return self.values.get(url,Page(url,status="NO_DATA"))


class FamilyIntelligenceTests(unittest.TestCase):
    def _init_identity_db(self,path):
        database.DB_PATH=path;main.DB_PATH=path
        database.init_platform_candidate_db();database.init_platform_family_db();database.init_platform_family_intelligence_db()

    def test_generic_url_core_word_parsing(self):
        cases=(
            ("https://console.novagrid.dev/products","novagrid.dev","console","novagrid"),
            ("https://docs.quantum-labs.co.uk/guide","quantum-labs.co.uk","docs","quantumlabs"),
            ("https://arc-node.io/","arc-node.io","","arcnode"),
        )
        for url,root,subdomain,core in cases:
            with self.subTest(url=url):
                self.assertEqual(domain_parts(url)[1:],(root,subdomain));self.assertEqual(extract_core_word(url,"Unrelated Title"),core)

    def test_derived_words_are_ordered_three_four_five_prefixes(self):
        self.assertEqual(derive_core_words("trade"),["tra","trad","trade"])
        self.assertEqual(derive_core_words("novagrid"),["nov","nova","novag"])
        self.assertEqual(derive_core_words("nova"),["nov","nova"])
        self.assertEqual(derive_core_words("ab"),[])

    def test_address_first_without_any_name_or_brand(self):
        search=Search();result=FamilyIntelligenceEngine(search=search,pages=Pages(),x_provider=Pages()).enrich(family())
        self.assertEqual(result["verification_status"],"NO_DATA")
        self.assertTrue(any(CREATOR in query or MEMBER in query for query in search.queries))
        self.assertGreaterEqual(len(search.queries),2)

    def test_pipeline_does_not_gate_empty_metadata_family(self):
        class Engine:
            def __init__(self):self.families=[]
            def enrich(self,value,auxiliary_candidates=()):
                self.families.append(value)
                return {"family_id":value["id"],"project_name":"","official_x":"","official_website":"","description":"","infrastructure_types":["Vault"],"official_token_symbol":"","official_token_ca":"","token_status":"NONE","confidence":0,"verification_status":"NO_DATA","discovered_candidates":0,"evidence":[]}
        engine=Engine()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"radar.db"
            with patch.object(database,"DB_PATH",path),patch.object(main,"DB_PATH",path):
                database.init_platform_candidate_db();database.init_platform_family_db();database.init_platform_family_intelligence_db()
                database.save_platform_candidate(MEMBER,CREATOR,100,"tx","","",["VAULT"],"test")
                database.replace_platform_families([{"creator":CREATOR,"first_block":100,"last_block":100,"member_count":1,"platform_types":["VAULT"],"brand_hint":"","members":[{"address":MEMBER,"block_number":100}]}])
                outcome=main.run_platform_identity_pipeline(engine=engine,protocols=[])
                self.assertEqual(outcome["families"],1);self.assertEqual(len(engine.families),1)
                self.assertEqual(engine.families[0]["token_names"],[]);self.assertIsNotNone(database.get_platform_family_intelligence(1))

    def test_no_family_but_website_found_runs_full_investigation(self):
        site="https://novagrid.dev";x="https://x.com/novagrid";docs="https://novagrid.dev/docs"
        pages=Pages({site:Page(site,text="NovaGrid HyperEVM router",links=[x,docs],title="NovaGrid",status="AVAILABLE"),
                     x:Page(x,text="NovaGrid router",links=[site],title="NovaGrid",status="AVAILABLE"),
                     docs:Page(docs,text="Protocol token information",status="AVAILABLE")})
        engine=FamilyIntelligenceEngine(search=Search(),pages=pages,x_provider=pages)
        seed={"id":None,"subject_key":"website:seed","creator":"","member_addresses":[],"member_count":0,"platform_types":"ROUTER","brand_hint":"","token_names":[],"token_symbols":[],"identity_urls":[site]}
        with tempfile.TemporaryDirectory() as tmp,patch.object(database,"DB_PATH",Path(tmp)/"radar.db"),patch.object(main,"DB_PATH",Path(tmp)/"radar.db"):
            self._init_identity_db(Path(tmp)/"radar.db")
            outcome=main.run_platform_identity_pipeline(engine=engine,protocols=[],standalone_seeds=[seed])
            self.assertEqual(outcome["families"],0);self.assertEqual(outcome["investigations"],1)
            self.assertEqual(outcome["confirmed"],1);self.assertEqual(len(outcome["notifications"]),1)
            row=database.sqlite3.connect(database.DB_PATH).execute("SELECT result FROM platform_identity_investigations").fetchone()
            saved=__import__("json").loads(row[0]);self.assertEqual(saved["core_word"],"novagrid");self.assertEqual(saved["derived_words"],["nov","nova","novag"]);self.assertIn(docs,saved["docs"])

    def test_family_with_no_search_candidates_continues_from_saved_website(self):
        site="https://quietvault.dev";engine=FamilyIntelligenceEngine(search=Search(),pages=Pages({site:Page(site,text="Vault product",status="AVAILABLE")}),x_provider=Pages())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"radar.db"
            with patch.object(database,"DB_PATH",path),patch.object(main,"DB_PATH",path):
                self._init_identity_db(path);database.save_platform_candidate(MEMBER,CREATOR,100,"tx","","",["VAULT"],"test")
                database.replace_platform_families([{"creator":CREATOR,"first_block":100,"last_block":100,"member_count":1,"platform_types":["VAULT"],"brand_hint":"","members":[{"address":MEMBER,"block_number":100}]}])
                with database.sqlite3.connect(path) as conn:conn.execute("UPDATE platform_families SET website_url=?",(site,));conn.commit()
                outcome=main.run_platform_identity_pipeline(engine=engine,protocols=[],standalone_seeds=[])
                saved=database.get_platform_family_intelligence(1)
                self.assertEqual(outcome["investigations"],1);self.assertEqual(saved["official_website"],site);self.assertEqual(saved["core_word"],"quietvault")

    def test_docs_reverse_family_address_evidence_is_linked(self):
        result={"subject_key":"website:reverse","family_id":None,"source_url":"https://site.invalid","verification_status":"PARTIAL","discovered_project_addresses":[{"address":MEMBER,"role":"ROUTER","source_url":"https://site.invalid/docs","evidence_source":"official_docs"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"radar.db"
            with patch.object(database,"DB_PATH",path),patch.object(main,"DB_PATH",path):
                self._init_identity_db(path);database.save_platform_candidate(MEMBER,CREATOR,100,"tx","","",["ROUTER"],"test")
                database.replace_platform_families([{"creator":CREATOR,"first_block":100,"last_block":100,"member_count":1,"platform_types":["ROUTER"],"brand_hint":"","members":[{"address":MEMBER,"block_number":100}]}])
                database.save_platform_identity_investigation(result)
                with database.sqlite3.connect(path) as conn:
                    row=conn.execute("SELECT family_id,role,evidence_source FROM platform_identity_address_evidence").fetchone()
                    investigation_family=conn.execute("SELECT family_id FROM platform_identity_investigations").fetchone()[0]
                self.assertEqual(row,(1,"ROUTER","official_docs"))
                self.assertEqual(investigation_family,1)

    def test_unverified_standalone_project_is_not_notified(self):
        site="https://candidate.dev";engine=FamilyIntelligenceEngine(search=Search(),pages=Pages({site:Page(site,text="Candidate",status="AVAILABLE")}),x_provider=Pages())
        seed={"id":None,"subject_key":"website:partial","creator":"","member_addresses":[],"member_count":0,"platform_types":"","brand_hint":"","token_names":[],"token_symbols":[],"identity_urls":[site]}
        with tempfile.TemporaryDirectory() as tmp,patch.object(database,"DB_PATH",Path(tmp)/"radar.db"),patch.object(main,"DB_PATH",Path(tmp)/"radar.db"):
            self._init_identity_db(Path(tmp)/"radar.db");outcome=main.run_platform_identity_pipeline(engine=engine,protocols=[],standalone_seeds=[seed])
            self.assertEqual(outcome["confirmed"],0);self.assertEqual(outcome["notifications"],[])

    def test_x_search_hit_is_candidate_not_verified(self):
        x="https://x.com/candidate"
        result=FamilyIntelligenceEngine(search=Search([SearchHit(x,source="test")]),pages=Pages(),x_provider=Pages()).enrich(family())
        self.assertEqual(result["official_x"],x);self.assertEqual(result["verification_status"],"PARTIAL")

    def test_website_x_cross_verification_and_address_evidence(self):
        site="https://project.example";x="https://x.com/project"
        pages=Pages({site:Page(site,text=f"Router protocol {MEMBER}",links=[x],title="Project | HyperEVM",description="Trade through our router",status="AVAILABLE"),
                     x:Page(x,text="Official Project profile and recent posts",links=[site],status="AVAILABLE")})
        result=FamilyIntelligenceEngine(search=Search([SearchHit(site,source="test"),SearchHit(x,source="test")]),pages=pages,x_provider=pages).enrich(family())
        self.assertEqual(result["verification_status"],"VERIFIED");self.assertEqual(result["project_name"],"Project")
        cross=next(x for x in result["evidence"] if x.get("kind")=="CROSS_VERIFICATION")
        self.assertTrue(cross["website_to_x"]);self.assertTrue(cross["x_to_website"])

    def test_address_found_website_drives_domain_search_for_x_and_docs(self):
        site="https://app.project.example";x="https://x.com/project";docs="https://docs.project.example/contracts"
        class DomainSearch(Search):
            def search(self,query,timeout):
                self.queries.append(query)
                if CREATOR in query or MEMBER in query:return [SearchHit(site,source="address")]
                if "project.example" in query:return [SearchHit(x,source="domain"),SearchHit(docs,source="domain")]
                return []
        search=DomainSearch();pages=Pages({site:Page(site,text="Project router",links=[],title="Project Protocol",status="AVAILABLE"),x:Page(x,status="NO_DATA"),docs:Page(docs,text="Documentation",status="AVAILABLE")})
        result=FamilyIntelligenceEngine(search=search,pages=pages,x_provider=pages).enrich(family())
        self.assertEqual(result["hostname"],"app.project.example");self.assertEqual(result["root_domain"],"project.example")
        self.assertEqual(result["subdomain"],"app");self.assertEqual(result["official_x"],x);self.assertIn(docs,result["docs"])

    def test_homepage_without_address_docs_address_verifies(self):
        site="https://project.example";docs="https://docs.project.example/deployments"
        pages=Pages({site:Page(site,text="Project lending",links=[docs],title="Project",status="AVAILABLE"),
                     docs:Page(docs,text=f"HyperEVM deployment {MEMBER}",status="AVAILABLE")})
        result=FamilyIntelligenceEngine(search=Search([SearchHit(site)]),pages=pages,x_provider=pages).enrich(family())
        self.assertEqual(result["verification_status"],"VERIFIED");self.assertEqual(result["verification_method"],"VERIFIED_BY_ADDRESS")

    def test_bidirectional_links_with_matching_product_identity_verify(self):
        site="https://project.example";x="https://x.com/project"
        pages=Pages({site:Page(site,text="Project builds a HyperEVM router",links=[x],title="Project Protocol",status="AVAILABLE"),
                     x:Page(x,text="Project router on HyperEVM",links=[site],title="Project",status="AVAILABLE")})
        result=FamilyIntelligenceEngine(search=Search([SearchHit(site),SearchHit(x)]),pages=pages,x_provider=pages).enrich(family())
        self.assertEqual(result["verification_status"],"VERIFIED");self.assertEqual(result["verification_method"],"VERIFIED_BY_CROSS_LINK")

    def test_domain_keyword_alone_does_not_verify(self):
        site="https://router-vault.example"
        result=FamilyIntelligenceEngine(search=Search([SearchHit(site)]),pages=Pages({site:Page(site,text="Welcome",status="AVAILABLE")}),x_provider=Pages()).enrich(family())
        self.assertEqual(result["verification_status"],"PARTIAL")

    def test_core_word_symbol_does_not_bind_an_unrelated_contract_address(self):
        site="https://novagrid.dev"
        pages=Pages({site:Page(site,text=f"Vault deployment {MEMBER}",title="Nova Grid",status="AVAILABLE")})
        result=FamilyIntelligenceEngine(search=Search([SearchHit(site)]),pages=pages,x_provider=pages).enrich(family(token_symbols=["NOV"]))
        self.assertEqual(result["core_word"],"novagrid");self.assertIn("nov",result["derived_words"])
        self.assertEqual(result["discovered_token_symbol"],"NOV");self.assertEqual(result["token_status"],"NONE")
        self.assertEqual(result["official_token_ca"],"")

    def test_symbol_similarity_is_partial_not_verified(self):
        result=discover_official_token("https://novagrid.dev","novagrid",["nov","nova","novag"],candidate_symbols=["NOV"],identity_verified=True)
        self.assertEqual(result["token_verification_status"],"PARTIAL");self.assertEqual(result["official_token_ca"],"")

    def test_official_website_token_association_verifies_ca_without_phrase_gate(self):
        sources=[{"kind":"website","url":"https://novagrid.dev","text":"NovaGrid launch information","token_symbol":"NOV","token_ca":TOKEN,"trusted":True}]
        result=discover_official_token("https://novagrid.dev","novagrid",["nov","nova","novag"],sources,identity_verified=True)
        self.assertEqual(result["token_verification_status"],"VERIFIED");self.assertEqual(result["official_token_ca"],TOKEN)

    def test_public_page_provider_populates_token_ca_and_engine_uses_it(self):
        site="https://novagrid.dev";token_page="https://novagrid.dev/assets/nov"
        site_raw=f'''<html><head><title>NovaGrid</title></head><body>HyperEVM router {MEMBER}</body></html>'''
        token_raw=f'''<html><script type="application/json">{{"asset":{{"name":"NovaGrid","symbol":"NOV","address":"{TOKEN}"}}}}</script></html>'''
        class Response:
            def __init__(self,url,text):self.url=url;self.text=text
            def raise_for_status(self):return None
        class TokenSearch(Search):
            def search(self,query,timeout):
                self.queries.append(query)
                if CREATOR in query or MEMBER in query:return [SearchHit(site)]
                if "site:novagrid.dev" in query:return [SearchHit(token_page)]
                return []
        def response(url,**kwargs):return Response(url,token_raw if url==token_page else site_raw)
        provider=PublicPageProvider()
        search=TokenSearch()
        with patch("platform_family_intelligence.requests.get",side_effect=response):
            result=FamilyIntelligenceEngine(search=search,pages=provider,x_provider=Pages()).enrich(family())
            page=provider.fetch(token_page,2)
        self.assertTrue(any('"novagrid" site:novagrid.dev' in query for query in search.queries))
        self.assertTrue(any('"nov" site:novagrid.dev' in query for query in search.queries))
        self.assertFalse(any("Token CA" in query for query in search.queries))
        self.assertEqual((page.token_name,page.token_symbol,page.token_ca),("NovaGrid","NOV",TOKEN))
        self.assertEqual(result["official_token_ca"],TOKEN);self.assertEqual(result["token_status"],"CONFIRMED_OFFICIAL")

    def test_official_docs_token_association_verifies_ca_without_phrase_gate(self):
        docs="https://docs.novagrid.dev/token";sources=[{"kind":"official_docs","url":docs,"text":"NovaGrid launch information","token_symbol":"NOV","token_ca":TOKEN,"trusted":True}]
        result=discover_official_token("https://novagrid.dev","novagrid",["nov","nova","novag"],sources,identity_verified=True)
        self.assertEqual(result["token_verification_status"],"VERIFIED");self.assertEqual(result["token_source_urls"],[docs])

    def test_confirmed_official_x_token_association_verifies_ca_without_phrase_gate(self):
        x="https://x.com/novagrid";sources=[{"kind":"official_x","url":x,"text":"NovaGrid launch information","token_symbol":"NOV","token_ca":TOKEN,"trusted":True}]
        result=discover_official_token("https://novagrid.dev","novagrid",["nov","nova","novag"],sources,identity_verified=True)
        self.assertEqual(result["token_verification_status"],"VERIFIED");self.assertEqual(result["official_token_ca"],TOKEN)

    def test_unrelated_token_deployer_is_not_a_confirmation_requirement(self):
        sources=[{"kind":"website","url":"https://novagrid.dev","text":"NovaGrid launch information","token_symbol":"NOV","token_ca":TOKEN,"trusted":True}]
        result=discover_official_token("https://novagrid.dev","novagrid",["nov","nova","novag"],sources,identity_verified=True)
        self.assertEqual(result["official_token_ca"],TOKEN)  # no Family/deployer input exists or is required

    def test_no_family_token_discovery_rejects_unbound_trusted_source_ca(self):
        weak=[{"kind":"website","url":"https://novagrid.dev","text":f"Router contract address: {TOKEN}","trusted":True}]
        accepted_weak=discover_official_token("https://novagrid.dev","novagrid",["nov","nova","novag"],weak,identity_verified=True)
        self.assertEqual(accepted_weak["token_verification_status"],"NO_DATA");self.assertEqual(accepted_weak["official_token_ca"],"")
        strong=[{"kind":"website","url":"https://novagrid.dev","text":"NovaGrid launch information","token_symbol":"NOV","token_ca":TOKEN,"trusted":True}]
        accepted=discover_official_token("https://novagrid.dev","novagrid",["nov","nova","novag"],strong,identity_verified=True)
        self.assertEqual(accepted["token_verification_status"],"VERIFIED")
        no_website=discover_official_token("","novagrid",["nov"],strong,candidate_symbols=["NOV"],identity_verified=True)
        self.assertEqual(no_website["token_verification_status"],"NO_DATA")

    def test_family_contract_addresses_are_not_automatic_token_ca(self):
        site="https://project.example"
        pages=Pages({site:Page(site,text=f"Vault contracts {MEMBER}; share token {TOKEN}",status="AVAILABLE")})
        result=FamilyIntelligenceEngine(search=Search([SearchHit(site)]),pages=pages,x_provider=pages).enrich(family(token_names=["Vault Share"],token_symbols=["vSHARE"],member_addresses=[MEMBER,TOKEN]))
        self.assertEqual(result["token_status"],"NONE");self.assertEqual(result["official_token_ca"],"")

    def test_explicit_official_contract_publication_confirms_ca(self):
        site="https://project.example"
        pages=Pages({site:Page(site,text=f"Contracts {MEMBER}. Project launch information",token_symbol="PRO",token_ca=TOKEN,status="AVAILABLE")})
        result=FamilyIntelligenceEngine(search=Search([SearchHit(site)]),pages=pages,x_provider=pages).enrich(family())
        self.assertEqual(result["token_status"],"CONFIRMED_OFFICIAL");self.assertEqual(result["official_token_ca"],TOKEN)

    def test_unrelated_deployer_token_confirmed_from_official_docs(self):
        site="https://project.example";docs="https://docs.project.example/contracts"
        pages=Pages({site:Page(site,text="Project vault",links=[docs],status="AVAILABLE"),
                     docs:Page(docs,text=f"Deployment {MEMBER}. Project launch information",token_symbol="PRO",token_ca=TOKEN,status="AVAILABLE")})
        result=FamilyIntelligenceEngine(search=Search([SearchHit(site)]),pages=pages,x_provider=pages).enrich(family())
        self.assertNotIn(TOKEN,family()["member_addresses"]);self.assertEqual(result["official_token_ca"],TOKEN)
        self.assertEqual(result["token_status"],"CONFIRMED_OFFICIAL")

    def test_provider_timeouts_degrade_and_scheduler_isolates_failure(self):
        result=FamilyIntelligenceEngine(search=Search(error=TimeoutError()),pages=Pages(error=TimeoutError()),x_provider=Pages(error=TimeoutError())).enrich(family())
        self.assertEqual(result["verification_status"],"NO_DATA")
        async def run():
            with patch("main.run_platform_identity_pipeline",side_effect=TimeoutError("network")):
                await request_identity_refresh()
        asyncio.run(run())  # no exception reaches the scanner event loop

    def test_structured_intelligence_persistence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(database,"DB_PATH",Path(tmp)/"radar.db"):
            database.init_platform_family_intelligence_db()
            value={"family_id":7,"project_name":"Project","official_x":"https://x.com/project","official_website":"https://app.project.example","description":"Router","infrastructure_types":["Router / Aggregator","Vault"],"official_token_symbol":"","official_token_ca":"","token_status":"NONE","confidence":55,"verification_status":"PARTIAL","verification_method":"PARTIAL","hostname":"app.project.example","root_domain":"project.example","subdomain":"app","docs":["https://docs.project.example"],"github":["https://github.com/project"],"source_url":"https://app.project.example","core_word":"project","derived_words":["prj","pro"],"discovered_token_name":"","discovered_token_symbol":"PRO","discovered_ca":"","evidence_source":"ONCHAIN_METADATA_CORE_WORD","evidence":[{"kind":"test"}]}
            self.assertTrue(database.save_platform_family_intelligence(value))
            saved=database.get_platform_family_intelligence(7)
            self.assertEqual(saved["infrastructure_types"],value["infrastructure_types"]);self.assertEqual(saved["token_status"],"NONE")
            self.assertEqual(saved["root_domain"],"project.example");self.assertEqual(saved["docs"],value["docs"])
            self.assertEqual(saved["core_word"],"project");self.assertEqual(saved["derived_words"],value["derived_words"])


if __name__ == "__main__":unittest.main()
