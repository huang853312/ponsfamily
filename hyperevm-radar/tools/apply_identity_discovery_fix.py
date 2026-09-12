from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "platform_family_intelligence.py"
text = PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


# 1) Search website, X and Docs directly from each chain address instead of
# making X/Docs wait for a website to be discovered first.
replace_once(
'''        for address in ordered_addresses[:3]:
            hits.extend(self._search(f'"{address}"',started,evidence))
            hits.extend(self._search(f'"{address}" HyperEVM OR Hyperliquid',started,evidence))
''',
'''        for address in ordered_addresses[:3]:
            hits.extend(self._search(f'"{address}"',started,evidence))
            hits.extend(self._search(f'"{address}" HyperEVM OR Hyperliquid',started,evidence))
            hits.extend(self._search(f'site:x.com "{address}"',started,evidence))
            hits.extend(self._search(f'"{address}" (docs OR documentation OR contracts)',started,evidence))
''',
"address parallel search",
)

# 2) Add conservative URL/page helpers. Parked domains must not become the
# project website merely because they happen to rank first in search.
anchor = '''def _project_addresses(page, evidence_source):
    if not page or page.status!="AVAILABLE": return []
    blob=page.raw+" "+page.text; out=[]; role_words=("factory","router","vault","oracle","pool")
    for match in ADDRESS_RE.finditer(blob):
        context=blob[max(0,match.start()-120):match.end()+120].lower()
        role=next((word.upper() for word in role_words if word in context),"")
        out.append({"address":match.group(0).lower(),"role":role,"source_url":page.url,"evidence_source":evidence_source})
    return out
'''
replacement = anchor + '''\nPARKED_PAGE_MARKERS = (\n    "domain for sale", "buy this domain", "this domain is for sale",\n    "afternic", "sedo domain parking", "hugedomains", "parkingcrew",\n)\n\ndef _is_docs_url(url):\n    host=_host(url); path=(urlparse(url).path or "").lower()\n    return host.startswith("docs.") or "docs" in host.split(".") or path.startswith("/docs") or "/docs/" in path\n\ndef _is_parked_page(page):\n    if not page or page.status!="AVAILABLE": return False\n    blob=" ".join((page.title,page.description,page.text[:5000])).lower()\n    return any(marker in blob for marker in PARKED_PAGE_MARKERS)\n'''
replace_once(anchor, replacement, "identity helpers")

# 3) Separate Docs candidates from ordinary websites, then inspect Docs
# immediately. Exact family-address evidence in Docs is retained even if the
# homepage has not been found yet. Docs links can also seed X/homepage search.
old = '''        unique=unique_hits(hits); x_urls=[]; web_hits=[]
        excluded={"github.com","t.me","telegram.me","youtube.com","x.com","twitter.com","dexscreener.com","coingecko.com","coinmarketcap.com","defillama.com","debank.com","etherscan.io"}
        for hit in unique:
            match=X_RE.search(hit.url)
            if match:
                x_urls.append(match.group(0)); evidence.append({"source":hit.source,"kind":"X_CANDIDATE","url":match.group(0),"title":hit.title[:160]})
            elif _host(hit.url) not in excluded:
                web_hits.append(hit)

        website_page=None; website_candidate=web_hits[0].url if web_hits else ""
'''
new = '''        unique=unique_hits(hits); x_urls=[]; docs_hits=[]; web_hits=[]
        excluded={"github.com","t.me","telegram.me","youtube.com","x.com","twitter.com","dexscreener.com","coingecko.com","coinmarketcap.com","defillama.com","debank.com","etherscan.io"}
        for hit in unique:
            match=X_RE.search(hit.url)
            if match:
                x_urls.append(match.group(0)); evidence.append({"source":hit.source,"kind":"X_CANDIDATE","url":match.group(0),"title":hit.title[:160]})
            elif _is_docs_url(hit.url):
                docs_hits.append(hit); evidence.append({"source":hit.source,"kind":"DOCS_CANDIDATE","url":hit.url,"title":hit.title[:160]})
            elif _host(hit.url) not in excluded:
                web_hits.append(hit)

        direct_docs_pages=[]
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

        website_page=None; website_candidate=""
'''
replace_once(old, new, "candidate classification and direct docs")

# 4) Reject parked pages and only remember a website candidate after the page
# was successfully fetched and passed the parked-domain check.
old = '''            if page.status!="AVAILABLE": continue
            page_blob=(page.raw+" "+page.text).lower(); page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(page_blob)})
            if page.address_match or any(X_RE.search(link) for link in page.links): website_page=page; break
            if website_page is None: website_page=page
'''
new = '''            if page.status!="AVAILABLE": continue
            if _is_parked_page(page):
                evidence.append({"source":"website","url":page.url,"status":"REJECTED_PARKED"}); continue
            if not website_candidate: website_candidate=page.url
            page_blob=(page.raw+" "+page.text).lower(); page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(page_blob)})
            if page.address_match or any(X_RE.search(link) for link in page.links): website_page=page; break
            if website_page is None: website_page=page
'''
replace_once(old, new, "parked website rejection")

# 5) Direct Docs with an exact Family address can participate in address-based
# verification immediately. Other Docs still require website/root linkage.
old = '''        trusted_docs=[]
        for link in docs[:4]:
            linked_by_site=bool(website_page and link in website_page.links); same_root=bool(root_domain and domain_parts(link)[1]==root_domain)
            if not (linked_by_site or same_root): continue
            try: page=self.pages.fetch(link,self.request_timeout)
'''
new = '''        trusted_docs=[page for page in direct_docs_pages if getattr(page,"address_match",False)]
        for link in docs[:4]:
            if any(page.url==link for page in trusted_docs): continue
            linked_by_site=bool(website_page and link in website_page.links); same_root=bool(root_domain and domain_parts(link)[1]==root_domain)
            if not (linked_by_site or same_root): continue
            try: page=self.pages.fetch(link,self.request_timeout)
'''
replace_once(old, new, "direct docs trust")

PATH.write_text(text, encoding="utf-8")
print("patched", PATH)
