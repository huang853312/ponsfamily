from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "platform_family_intelligence.py"
TEST = ROOT / "tests" / "test_identity_token_strong.py"
text = TARGET.read_text()

old = '''def _same_docs_space(a,b):
    ha,hb=_host(a),_host(b)
    if not ha or not hb: return False
    if ha==hb: return True
    return bool(ha.endswith(".gitbook.io") and hb.endswith(".gitbook.io") and ha==hb)
'''
new = '''def _same_docs_space(a,b):
    ha,hb=_host(a),_host(b)
    if not ha or not hb: return False
    if ha==hb: return True
    return bool(ha.endswith(".gitbook.io") and hb.endswith(".gitbook.io") and ha==hb)

TOKEN_DETAIL_MARKERS = ("token", "tokens", "contract", "contracts", "address", "addresses", "deployment", "deployments", "governance", "airdrop", "rewards")

def _looks_like_token_detail_url(url, root_domain=""):
    if not url.startswith(("http://","https://")): return False
    if root_domain and domain_parts(url)[1] != root_domain: return False
    parsed=urlparse(url); blob=((parsed.path or "")+" "+(parsed.query or "")).lower()
    return any(marker in blob for marker in TOKEN_DETAIL_MARKERS)

def _page_contains_address(page,address):
    if not page or page.status!="AVAILABLE" or not ADDRESS_RE.fullmatch(str(address or "")): return False
    return str(address).lower() in (page.raw+" "+page.text).lower()

def _contextual_token_sources(page, clues, kind, trusted=False):
    if not page or page.status!="AVAILABLE": return []
    blob=page.raw+" "+page.text; lower=blob.lower(); normalized_clues=[re.sub(r"[^a-z0-9]","",str(x).lower()) for x in clues if x]
    out=[]
    for match in ADDRESS_RE.finditer(blob):
        context=lower[max(0,match.start()-220):match.end()+220]
        norm_context=re.sub(r"[^a-z0-9]","",context)
        clue=next((x for x in normalized_clues if x and x in norm_context),"")
        explicit=any(marker in context for marker in ("token address","token contract","contract address","ticker","symbol","official token","governance token"))
        if not clue and not explicit: continue
        out.append({"kind":kind,"url":page.url,"token_name":"","token_symbol":"","token_ca":match.group(0).lower(),"trusted":bool(trusted),"context_clue":clue,"explicit_token_context":explicit})
    return out
'''
assert old in text, "helper anchor not found"
text=text.replace(old,new,1)

old = '''        for address in ordered_addresses[:3]:
            direct_hits,new_clues=self._direct_address_identity(address,started,evidence)
            hits.extend(direct_hits); direct_clues.extend(new_clues)
            hits.extend(self._search(f'"{address}"',started,evidence))
            hits.extend(self._search(f'"{address}" HyperEVM OR Hyperliquid',started,evidence))
            hits.extend(self._search(f'site:x.com "{address}"',started,evidence))
'''
new = '''        for address in ordered_addresses[:3]:
            direct_hits,new_clues=self._direct_address_identity(address,started,evidence)
            hits.extend(direct_hits); direct_clues.extend(new_clues)
            hits.extend(self._search(f'"{address}"',started,evidence))
            hits.extend(self._search(f'"{address}" HyperEVM OR Hyperliquid',started,evidence))
            hits.extend(self._search(f'site:x.com "{address}"',started,evidence))
        # Strengthen early identity discovery without changing the canonical flow:
        # inspect more Family members directly, but keep the expensive generic search
        # budget concentrated on the first three addresses.
        for address in ordered_addresses[3:8]:
            if time.monotonic()-started>=self.total_timeout: break
            direct_hits,new_clues=self._direct_address_identity(address,started,evidence)
            hits.extend(direct_hits); direct_clues.extend(new_clues)
        for address in ordered_addresses[:6]:
            hits.extend(self._search(f'site:github.com "{address}" HyperEVM',started,evidence))
            if time.monotonic()-started>=self.total_timeout: break
'''
assert old in text, "identity address anchor not found"
text=text.replace(old,new,1)

old = '''        unique=unique_hits(hits); x_urls=[]; docs_hits=[]; web_hits=[]
        excluded={"github.com","t.me","telegram.me","youtube.com","x.com","twitter.com","dexscreener.com","coingecko.com","coinmarketcap.com","defillama.com","debank.com","etherscan.io"}
'''
new = '''        unique=unique_hits(hits); x_urls=[]; docs_hits=[]; web_hits=[]; github_hits=[]
        excluded={"github.com","t.me","telegram.me","youtube.com","x.com","twitter.com","dexscreener.com","coingecko.com","coinmarketcap.com","defillama.com","debank.com","etherscan.io"}
'''
assert old in text, "unique anchor not found"
text=text.replace(old,new,1)

old = '''            elif _is_docs_url(hit.url):
                docs_hits.append(hit); evidence.append({"source":hit.source,"kind":"DOCS_CANDIDATE","url":hit.url,"title":hit.title[:160]})
            elif _host(hit.url) not in excluded:
                web_hits.append(hit)

        direct_docs_pages=[]
'''
new = '''            elif _is_docs_url(hit.url):
                docs_hits.append(hit); evidence.append({"source":hit.source,"kind":"DOCS_CANDIDATE","url":hit.url,"title":hit.title[:160]})
            elif _host(hit.url)=="github.com":
                github_hits.append(hit); evidence.append({"source":hit.source,"kind":"GITHUB_CANDIDATE","url":hit.url,"title":hit.title[:160]})
            elif _host(hit.url) not in excluded:
                web_hits.append(hit)

        # GitHub is discovery-only. It may reveal an official website/X/Docs backlink,
        # but GitHub alone never upgrades a project to VERIFIED.
        for hit in github_hits[:4]:
            if time.monotonic()-started>=self.total_timeout: break
            try: page=self.pages.fetch(hit.url,self.request_timeout)
            except Exception as exc:
                evidence.append({"source":"github_discovery","url":hit.url,"status":"NO_DATA","detail":type(exc).__name__}); continue
            if page.status!="AVAILABLE": continue
            for link in page.links:
                match=X_RE.search(link)
                if match:
                    x_urls.append(match.group(0)); evidence.append({"source":"github_discovery","kind":"X_CANDIDATE","url":match.group(0)})
                elif _is_docs_url(link):
                    docs_hits.append(SearchHit(link,source="GITHUB_BACKLINK"))
                elif link.startswith(("http://","https://")) and _host(link) not in excluded:
                    web_hits.insert(0,SearchHit(link,source="GITHUB_BACKLINK"))

        direct_docs_pages=[]
'''
assert old in text, "github classify anchor not found"
text=text.replace(old,new,1)

old = '''        token_pages=[]; token_hits=[]; token_clues=list(dict.fromkeys(x for x in (core_word,*derived_words) if x)); token_targets=[f"site:{root_domain}"] if root_domain else []
        chain_token_candidates=[]
'''
new = '''        token_pages=[]; token_hits=[]; token_clues=list(dict.fromkeys(x for x in (core_word,*derived_words) if x)); token_targets=[f"site:{root_domain}"] if root_domain else []
        # Preserve the original full core_word + 3/4/5 rule exactly. Add direct
        # crawling of official token/contract/address pages so discovery does not
        # depend on search-engine indexing.
        if website_page and root_domain:
            token_queue=[link for link in website_page.links if _looks_like_token_detail_url(link,root_domain)][:8]
            seen_token_pages=set()
            while token_queue and len(token_pages)<8:
                if time.monotonic()-started>=self.total_timeout: break
                url=token_queue.pop(0).split("#",1)[0]
                if not url or url in seen_token_pages: continue
                seen_token_pages.add(url)
                try: page=self.pages.fetch(url,self.request_timeout)
                except Exception as exc:
                    evidence.append({"source":"official_token_page","url":url,"status":"NO_DATA","detail":type(exc).__name__}); continue
                if page.status!="AVAILABLE": continue
                token_pages.append((page,"official_docs" if _is_docs_url(page.url) else "website"))
                evidence.append({"source":"official_token_page","url":page.url,"status":"AVAILABLE"})
                for link in page.links:
                    if _looks_like_token_detail_url(link,root_domain) and link.split("#",1)[0] not in seen_token_pages:
                        token_queue.append(link)
        chain_token_candidates=[]
'''
assert old in text, "token start anchor not found"
text=text.replace(old,new,1)

old = '''        if x_url: token_targets.append(f"site:x.com/{urlparse(x_url).path.strip('/').split('/')[0]}")
        for clue in token_clues:
            for target in token_targets:
                token_hits.extend(self._search(f'"{clue}" {target} (token OR contract OR CA)',started,evidence))
'''
new = '''        if x_url: token_targets.append(f"site:x.com/{urlparse(x_url).path.strip('/').split('/')[0]}")
        for clue in token_clues:
            for target in token_targets:
                token_hits.extend(self._search(f'"{clue}" {target} (token OR contract OR CA)',started,evidence))
            token_hits.extend(self._search(f'"{clue}" site:github.com (token OR address OR deployment OR config)',started,evidence))
        # Reverse-check DEX candidates against official sources by exact CA. DEX is
        # still candidate-only; exact appearance on the official site/X is the trust step.
        for candidate in chain_token_candidates[:6]:
            ca=candidate["token_ca"]
            if root_domain:
                token_hits.extend(self._search(f'"{ca}" site:{root_domain}',started,evidence))
            if x_url:
                handle=urlparse(x_url).path.strip('/').split('/')[0]
                if handle: token_hits.extend(self._search(f'"{ca}" site:x.com/{handle}',started,evidence))
'''
assert old in text, "token search anchor not found"
text=text.replace(old,new,1)

old = '''            if page.status=="AVAILABLE": token_pages.append((page,"official_x" if is_x else "official_docs" if "docs" in _host(page.url) or "/docs" in page.url else "website"))

        token_sources=list(chain_token_candidates)
'''
new = '''            if page.status=="AVAILABLE": token_pages.append((page,"official_x" if is_x else "official_docs" if "docs" in _host(page.url) or "/docs" in page.url else "website"))

        token_sources=list(chain_token_candidates)
        # Extract CA candidates from context on official pages. This supplements the
        # existing structured JSON metadata parser; it does not replace it.
        if website_page:
            token_sources.extend(_contextual_token_sources(website_page,token_clues,"website",True))
        for page in trusted_docs:
            token_sources.extend(_contextual_token_sources(page,token_clues,"official_docs",True))
        if x_page.status=="AVAILABLE" and (site_links_x or x_links_site):
            token_sources.extend(_contextual_token_sources(x_page,token_clues,"official_x",True))
        for page,kind in token_pages:
            trusted=kind!="official_x" or site_links_x or x_links_site
            if kind in {"website","official_docs","official_x"}:
                token_sources.extend(_contextual_token_sources(page,token_clues,kind,trusted))
        # If an exact DEX candidate CA appears on a trusted official page, bind the
        # candidate symbol/name to that official evidence and allow normal verification.
        trusted_pages=[(website_page,"website",True)] if website_page else []
        trusted_pages += [(p,"official_docs",True) for p in trusted_docs]
        if x_page.status=="AVAILABLE": trusted_pages.append((x_page,"official_x",site_links_x or x_links_site))
        trusted_pages += [(p,k,k!="official_x" or site_links_x or x_links_site) for p,k in token_pages if k in {"website","official_docs","official_x"}]
        for candidate in chain_token_candidates[:8]:
            for page,kind,trusted in trusted_pages:
                if trusted and _page_contains_address(page,candidate["token_ca"]):
                    token_sources.append({"kind":kind,"url":page.url,"token_name":candidate.get("token_name","") ,"token_symbol":candidate.get("token_symbol","") ,"token_ca":candidate["token_ca"],"trusted":True,"matched_from":"dex_candidate_exact_ca"})
                    break
'''
assert old in text, "token source anchor not found"
text=text.replace(old,new,1)

TARGET.write_text(text)

TEST.write_text('''import unittest\nfrom unittest.mock import patch\n\nfrom platform_family_intelligence import FamilyIntelligenceEngine, Page, PageProvider, SearchHit, SearchProvider, XContentProvider\n\nCREATOR="0x"+"11"*20\nM1="0x"+"22"*20\nM2="0x"+"33"*20\nM3="0x"+"44"*20\nTOKEN="0x"+"55"*20\n\nclass Search(SearchProvider):\n    def __init__(self,resolver=None): self.resolver=resolver or (lambda q: []); self.queries=[]\n    def search(self,q,timeout): self.queries.append(q); return list(self.resolver(q))\n\nclass Pages(PageProvider,XContentProvider):\n    def __init__(self,values): self.values=values\n    def fetch(self,url,timeout): return self.values.get(url,Page(url,status="NO_DATA"))\n\ndef fam():\n    return {"id":501,"creator":CREATOR,"member_addresses":[M1,M2,M3],"member_count":4,"platform_types":"ROUTER,VAULT","brand_hint":"","token_names":[],"token_symbols":[]}\n\nclass IdentityTokenStrongTests(unittest.TestCase):\n    def test_later_family_member_direct_metadata_can_recover_identity(self):\n        site="https://laterfam.dev"; x="https://x.com/laterfam"\n        pages=Pages({site:Page(site,text=f"LaterFam HyperEVM router {M3}",links=[x],title="LaterFam",status="AVAILABLE"),x:Page(x,text="LaterFam HyperEVM",links=[site],title="LaterFam",status="AVAILABLE")})\n        class Response:\n            def raise_for_status(self): pass\n            def json(self): return [{"info":{"websites":[{"url":site}],"socials":[{"platform":"twitter","handle":"laterfam"}]},"baseToken":{"address":M3,"name":"LaterFam","symbol":"LATE"}}]\n        def fake_get(url,*args,**kwargs):\n            if M3 in url: return Response()\n            raise RuntimeError("no metadata")\n        with patch("platform_family_intelligence.requests.get",side_effect=fake_get):\n            result=FamilyIntelligenceEngine(search=Search(),pages=pages,x_provider=pages,total_timeout=90).enrich(fam())\n        self.assertEqual(result["official_website"],site)\n        self.assertEqual(result["verification_status"],"VERIFIED")\n\n    def test_official_linked_token_page_finds_ca_without_search_index(self):\n        site="https://novagrid.dev"; token_page=site+"/token"; x="https://x.com/novagrid"\n        pages=Pages({site:Page(site,text=f"NovaGrid HyperEVM router {M1}",links=[x,token_page],title="NovaGrid",status="AVAILABLE"),x:Page(x,text="NovaGrid HyperEVM",links=[site],title="NovaGrid",status="AVAILABLE"),token_page:Page(token_page,text=f"Official NovaGrid governance token NOV contract address {TOKEN}",title="NovaGrid Token",status="AVAILABLE")})\n        result=FamilyIntelligenceEngine(search=Search(lambda q:[SearchHit(site)] if M1 in q else []),pages=pages,x_provider=pages,total_timeout=90).enrich(fam())\n        self.assertEqual(result["derived_words"],["nov","nova","novag"])\n        self.assertEqual(result["official_token_ca"],TOKEN)\n        self.assertEqual(result["token_status"],"CONFIRMED_OFFICIAL")\n\n    def test_dex_candidate_needs_exact_official_reverse_match(self):\n        site="https://novagrid.dev"; x="https://x.com/novagrid"\n        pages=Pages({site:Page(site,text=f"NovaGrid HyperEVM router {M1}. Official token address {TOKEN}",links=[x],title="NovaGrid",status="AVAILABLE"),x:Page(x,text="NovaGrid HyperEVM",links=[site],title="NovaGrid",status="AVAILABLE")})\n        class Response:\n            def __init__(self,payload): self.payload=payload\n            def raise_for_status(self): pass\n            def json(self): return self.payload\n        def fake_get(url,*args,**kwargs):\n            if "latest/dex/search" in url: return Response({"pairs":[{"chainId":"hyperevm","url":"https://dexscreener.com/hyperevm/pair","pairAddress":"0x"+"66"*20,"baseToken":{"address":TOKEN,"name":"NovaGrid","symbol":"NOV"},"quoteToken":{"address":"0x"+"77"*20,"name":"WHYPE","symbol":"WHYPE"}}]})\n            raise RuntimeError("no direct metadata")\n        with patch("platform_family_intelligence.requests.get",side_effect=fake_get):\n            result=FamilyIntelligenceEngine(search=Search(lambda q:[SearchHit(site)] if M1 in q else []),pages=pages,x_provider=pages,total_timeout=90).enrich(fam())\n        self.assertEqual(result["official_token_ca"],TOKEN)\n        self.assertEqual(result["token_status"],"CONFIRMED_OFFICIAL")\n\n    def test_three_four_five_search_rule_is_preserved(self):\n        site="https://novagrid.dev"\n        search=Search(lambda q:[SearchHit(site)] if M1 in q else [])\n        pages=Pages({site:Page(site,text=f"NovaGrid HyperEVM router {M1}",title="NovaGrid",status="AVAILABLE")})\n        FamilyIntelligenceEngine(search=search,pages=pages,x_provider=pages,total_timeout=90).enrich(fam())\n        joined="\\n".join(search.queries)\n        for clue in ("novagrid","nov","nova","novag"):\n            self.assertIn(f'"{clue}"',joined)\n\nif __name__=="__main__": unittest.main()\n''')
print("Patched",TARGET)
print("Wrote",TEST)
