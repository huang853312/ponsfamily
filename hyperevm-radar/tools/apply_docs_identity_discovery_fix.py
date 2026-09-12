from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "platform_family_intelligence.py"
TEST = ROOT / "tests" / "test_docs_identity_discovery.py"
text = TARGET.read_text()

old = '''def _is_docs_url(url):
    host=_host(url); path=(urlparse(url).path or "").lower()
    return host.startswith("docs.") or "docs" in host.split(".") or path.startswith("/docs") or "/docs/" in path
'''
new = '''def _is_docs_url(url):
    host=_host(url); path=(urlparse(url).path or "").lower()
    return host.startswith("docs.") or "docs" in host.split(".") or host.endswith("gitbook.io") or path.startswith("/docs") or "/docs/" in path

DOC_DETAIL_MARKERS = ("contract", "contracts", "address", "addresses", "deployment", "deployments", "hyperevm", "hyperliquid", "chain-999", "chain_999")

def _looks_like_docs_detail_url(url):
    if not _is_docs_url(url): return False
    parsed=urlparse(url); blob=((parsed.path or "")+" "+(parsed.query or "")).lower()
    return any(marker in blob for marker in DOC_DETAIL_MARKERS)

def _same_docs_space(a,b):
    ha,hb=_host(a),_host(b)
    if not ha or not hb: return False
    if ha==hb: return True
    return bool(ha.endswith(".gitbook.io") and hb.endswith(".gitbook.io") and ha==hb)
'''
assert old in text, "docs helper anchor not found"
text = text.replace(old, new, 1)

old = '''        for address in ordered_addresses[:3]:
            direct_hits,new_clues=self._direct_address_identity(address,started,evidence)
            hits.extend(direct_hits); direct_clues.extend(new_clues)
            hits.extend(self._search(f'"{address}"',started,evidence))
            hits.extend(self._search(f'"{address}" HyperEVM OR Hyperliquid',started,evidence))
            hits.extend(self._search(f'site:x.com "{address}"',started,evidence))
            hits.extend(self._search(f'"{address}" (docs OR documentation OR contracts)',started,evidence))
'''
new = '''        for address in ordered_addresses[:3]:
            direct_hits,new_clues=self._direct_address_identity(address,started,evidence)
            hits.extend(direct_hits); direct_clues.extend(new_clues)
            hits.extend(self._search(f'"{address}"',started,evidence))
            hits.extend(self._search(f'"{address}" HyperEVM OR Hyperliquid',started,evidence))
            hits.extend(self._search(f'site:x.com "{address}"',started,evidence))
        # Docs discovery is broader than generic web identity discovery. Search the
        # creator plus the whole Family (bounded for latency), because official docs
        # often publish only one Router/Factory/Vault address and not the first three.
        for address in ordered_addresses[:12]:
            hits.extend(self._search(
                f'"{address}" (docs OR documentation OR contracts OR deployments OR addresses OR HyperEVM)',
                started,evidence,
            ))
            if time.monotonic()-started>=self.total_timeout: break
        for address in ordered_addresses[:6]:
            hits.extend(self._search(f'site:gitbook.io "{address}"',started,evidence))
            if time.monotonic()-started>=self.total_timeout: break
'''
assert old in text, "address search anchor not found"
text = text.replace(old, new, 1)

old = '''        direct_docs_pages=[]
        for hit in docs_hits[:4]:
            if time.monotonic()-started>=self.total_timeout: break
            try: page=self.pages.fetch(hit.url,self.request_timeout)
            except Exception as exc:
                evidence.append({"source":"direct_docs","url":hit.url,"status":"NO_DATA","detail":type(exc).__name__}); continue
            if page.status!="AVAILABLE": continue
            blob=(page.raw+" "+page.text).lower(); page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(blob)})
            direct_docs_pages.append(page)
            evidence.append({"source":"direct_docs","url":page.url,"status":"AVAILABLE","address_match":page.address_match})
            for link in page.links:
                match=X_RE.search(link)
                if match:
                    x_urls.append(match.group(0))
                    evidence.append({"source":"direct_docs","kind":"X_CANDIDATE","url":match.group(0)})
                    continue
                if link.startswith(("http://","https://")) and _host(link) not in excluded and not _is_docs_url(link):
                    web_hits.insert(0,SearchHit(link,source="DOCS_BACKLINK"))
'''
new = '''        direct_docs_pages=[]
        seen_docs=set()
        docs_queue=[hit.url for hit in docs_hits[:8]]
        # Crawl one shallow layer of likely contract/address/deployment pages. This
        # turns a Docs landing-page hit into the exact page that contains Family CAs.
        while docs_queue and len(direct_docs_pages)<12:
            if time.monotonic()-started>=self.total_timeout: break
            docs_url=docs_queue.pop(0).split("#",1)[0]
            if not docs_url or docs_url in seen_docs: continue
            seen_docs.add(docs_url)
            try: page=self.pages.fetch(docs_url,self.request_timeout)
            except Exception as exc:
                evidence.append({"source":"direct_docs","url":docs_url,"status":"NO_DATA","detail":type(exc).__name__}); continue
            if page.status!="AVAILABLE": continue
            blob=(page.raw+" "+page.text).lower(); page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(blob)})
            direct_docs_pages.append(page)
            evidence.append({"source":"direct_docs","url":page.url,"status":"AVAILABLE","address_match":page.address_match})
            for link in page.links:
                if _same_docs_space(link,page.url) and _looks_like_docs_detail_url(link) and link.split("#",1)[0] not in seen_docs:
                    docs_queue.append(link)
                    continue
                match=X_RE.search(link)
                if match:
                    x_urls.append(match.group(0))
                    evidence.append({"source":"direct_docs","kind":"X_CANDIDATE","url":match.group(0)})
                    continue
                if link.startswith(("http://","https://")) and _host(link) not in excluded and not _is_docs_url(link):
                    web_hits.insert(0,SearchHit(link,source="DOCS_BACKLINK"))
'''
assert old in text, "direct docs anchor not found"
text = text.replace(old, new, 1)

old = '''        all_links=[hit.url for hit in unique]+(website_page.links if website_page else [])+(x_page.links if x_page.status=="AVAILABLE" else [])
        docs=list(dict.fromkeys(link for link in all_links if "docs" in _host(link) or "/docs" in link))
        github=list(dict.fromkeys(link for link in all_links if _host(link)=="github.com"))
        trusted_docs=[page for page in direct_docs_pages if getattr(page,"address_match",False)]
        for link in docs[:4]:
            if any(page.url==link for page in trusted_docs): continue
            linked_by_site=bool(website_page and link in website_page.links); same_root=bool(root_domain and domain_parts(link)[1]==root_domain)
            if not (linked_by_site or same_root): continue
            try: page=self.pages.fetch(link,self.request_timeout)
            except Exception as exc: evidence.append({"source":"official_docs","url":link,"status":"NO_DATA","detail":type(exc).__name__}); continue
            if page.status=="AVAILABLE":
                blob=(page.raw+" "+page.text).lower(); page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(blob)}); trusted_docs.append(page); evidence.append({"source":"official_docs","url":page.url,"address_match":page.address_match})
        docs_address=any(getattr(page,"address_match",False) for page in trusted_docs)
'''
new = '''        all_links=[hit.url for hit in unique]+[p.url for p in direct_docs_pages]+(website_page.links if website_page else [])+(x_page.links if x_page.status=="AVAILABLE" else [])
        docs=list(dict.fromkeys(link for link in all_links if _is_docs_url(link)))
        github=list(dict.fromkeys(link for link in all_links if _host(link)=="github.com"))
        trusted_docs=[]
        docs_crosslinked=False
        for page in direct_docs_pages:
            linked_by_site=bool(website_page and any(_same_docs_space(link,page.url) for link in website_page.links if _is_docs_url(link)))
            same_root=bool(root_domain and domain_parts(page.url)[1]==root_domain)
            docs_to_site=bool(website and any(_same_site(link,website) for link in page.links))
            docs_to_x=bool(x_url and any((m:=X_RE.search(link)) and m.group(0).rstrip("/").lower()==x_url.rstrip("/").lower() for link in page.links))
            if linked_by_site or same_root or docs_to_site or docs_to_x:
                trusted_docs.append(page); docs_crosslinked=True
                evidence.append({"source":"official_docs","url":page.url,"address_match":getattr(page,"address_match",False),"crosslinked":True})
        for link in docs[:6]:
            if any(page.url==link for page in direct_docs_pages): continue
            linked_by_site=bool(website_page and link in website_page.links); same_root=bool(root_domain and domain_parts(link)[1]==root_domain)
            if not (linked_by_site or same_root): continue
            try: page=self.pages.fetch(link,self.request_timeout)
            except Exception as exc: evidence.append({"source":"official_docs","url":link,"status":"NO_DATA","detail":type(exc).__name__}); continue
            if page.status=="AVAILABLE":
                blob=(page.raw+" "+page.text).lower(); page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(blob)}); trusted_docs.append(page); docs_crosslinked=True; evidence.append({"source":"official_docs","url":page.url,"address_match":page.address_match,"crosslinked":True})
        docs_address=any(getattr(page,"address_match",False) for page in direct_docs_pages+trusted_docs)
'''
assert old in text, "docs trust anchor not found"
text = text.replace(old, new, 1)

old = '''        if homepage_address or docs_address: verification="VERIFIED"; verification_method="VERIFIED_BY_ADDRESS"
'''
new = '''        if homepage_address or (docs_address and docs_crosslinked): verification="VERIFIED"; verification_method="VERIFIED_BY_ADDRESS"
'''
assert old in text, "verification anchor not found"
text = text.replace(old, new, 1)

old = '''        evidence.append({"kind":"CROSS_VERIFICATION","website_to_x":site_links_x,"x_to_website":x_links_site,"brand_consistent":brand_consistent,"chain_context":chain_context,"product_context":product_context,"family_address_on_website":homepage_address,"family_address_in_docs":docs_address,"status":verification_method})
'''
new = '''        evidence.append({"kind":"CROSS_VERIFICATION","website_to_x":site_links_x,"x_to_website":x_links_site,"brand_consistent":brand_consistent,"chain_context":chain_context,"product_context":product_context,"family_address_on_website":homepage_address,"family_address_in_docs":docs_address,"docs_crosslinked":docs_crosslinked,"status":verification_method})
'''
assert old in text, "cross verification anchor not found"
text = text.replace(old, new, 1)

TARGET.write_text(text)

TEST.write_text('''import unittest\n\nfrom platform_family_intelligence import FamilyIntelligenceEngine, Page, PageProvider, SearchHit, SearchProvider, XContentProvider\n\nCREATOR = "0x" + "11" * 20\nMEMBER1 = "0x" + "22" * 20\nMEMBER2 = "0x" + "33" * 20\nMEMBER3 = "0x" + "44" * 20\n\nclass Search(SearchProvider):\n    def __init__(self, resolver): self.resolver=resolver; self.queries=[]\n    def search(self, query, timeout): self.queries.append(query); return list(self.resolver(query))\n\nclass Pages(PageProvider, XContentProvider):\n    def __init__(self, values): self.values=values\n    def fetch(self, url, timeout): return self.values.get(url, Page(url,status="NO_DATA"))\n\ndef fam():\n    return {"id":301,"creator":CREATOR,"member_addresses":[MEMBER1,MEMBER2,MEMBER3],"member_count":4,"platform_types":"ROUTER,VAULT","brand_hint":"","token_names":[],"token_symbols":[]}\n\nclass DocsIdentityDiscoveryTests(unittest.TestCase):\n    def test_searches_creator_and_later_family_members_for_docs_and_crawls_deployment_page(self):\n        docs="https://docs.deepinfra.dev"; deploy=docs+"/contracts/deployments"; site="https://deepinfra.dev"; x="https://x.com/deepinfra"\n        def resolver(query):\n            if MEMBER3 in query and "deployments" in query: return [SearchHit(docs,source="docs-search")]\n            return []\n        pages=Pages({\n            docs:Page(docs,text="DeepInfra docs",links=[deploy,site,x],title="DeepInfra Docs",status="AVAILABLE"),\n            deploy:Page(deploy,text=f"HyperEVM Router {MEMBER3}",links=[site,x],title="Deployments",status="AVAILABLE"),\n            site:Page(site,text="DeepInfra HyperEVM router",links=[x,docs],title="DeepInfra",status="AVAILABLE"),\n            x:Page(x,text="DeepInfra HyperEVM",links=[site],title="DeepInfra",status="AVAILABLE"),\n        })\n        search=Search(resolver)\n        result=FamilyIntelligenceEngine(search=search,pages=pages,x_provider=pages,total_timeout=90).enrich(fam())\n        self.assertEqual(result["verification_status"],"VERIFIED")\n        self.assertEqual(result["official_website"],site)\n        self.assertIn(deploy,result["docs"])\n        self.assertTrue(any(MEMBER3 in q and "deployments" in q for q in search.queries))\n        self.assertTrue(any(item.get("docs_crosslinked") for item in result["evidence"] if item.get("kind")=="CROSS_VERIFICATION"))\n\n    def test_orphan_docs_address_alone_does_not_verify(self):\n        docs="https://lonely.gitbook.io/project/contracts"\n        def resolver(query):\n            if CREATOR in query and "deployments" in query: return [SearchHit(docs,source="docs-search")]\n            return []\n        pages=Pages({docs:Page(docs,text=f"HyperEVM contract {CREATOR}",links=[],title="Lonely Docs",status="AVAILABLE")})\n        result=FamilyIntelligenceEngine(search=Search(resolver),pages=pages,x_provider=pages,total_timeout=90).enrich(fam())\n        self.assertNotEqual(result["verification_status"],"VERIFIED")\n        cross=next(x for x in result["evidence"] if x.get("kind")=="CROSS_VERIFICATION")\n        self.assertTrue(cross["family_address_in_docs"])\n        self.assertFalse(cross["docs_crosslinked"])\n\nif __name__ == "__main__": unittest.main()\n''')
print("Patched", TARGET)
print("Wrote", TEST)
