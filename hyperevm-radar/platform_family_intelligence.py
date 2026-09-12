"""Address-first, provider-based identity enrichment for Platform Families.

This module performs no database writes and never treats an on-chain relationship as
proof of an official platform token.  It is intentionally synchronous because the
existing scheduler runs the complete identity job in ``asyncio.to_thread``.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests


ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
X_RE = re.compile(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})(?:[^\s\"'<>]*)?", re.I)
META_RE = re.compile(r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)', re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
TOKEN_SYMBOL_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,14})|\(([A-Z][A-Z0-9]{1,14})\)")
JSON_SCRIPT_RE = re.compile(r'<script[^>]+type=["\']application/(?:ld\+)?json["\'][^>]*>(.*?)</script>', re.I | re.S)


TYPE_RULES = {
    "Launchpad": ("launchpad", "token launcher", "bonding curve"),
    "AMM / DEX": (" amm ", " dex ", "swap", "liquidity pool", "exchange"),
    "Router / Aggregator": ("router", "aggregator", "route trades"),
    "Vault": ("vault", "yield strategy", "allocator"),
    "Lending": ("lending", "borrow", "money market", "liquidation"),
    "Oracle": ("oracle", "price feed", "data feed"),
    "RWA / Tokenized Assets": ("rwa", "real world asset", "tokenized asset", "tokenized stock"),
    "Derivatives / Perps": ("derivative", "perpetual", "perps", "options"),
    "Staking / LST / LRT": ("staking", "liquid staking", "restaking", " lst ", " lrt "),
    "Wallet": ("wallet", "smart account", "account abstraction"),
    "Dev Infrastructure": ("developer infrastructure", " rpc ", " sdk ", "indexer", " api "),
}
ROLE_TYPES = {
    "LAUNCHPAD": "Launchpad", "DEX_AMM": "AMM / DEX", "AMM": "AMM / DEX",
    "ROUTER": "Router / Aggregator", "AGGREGATOR": "Router / Aggregator",
    "VAULT": "Vault", "LENDING": "Lending", "ORACLE": "Oracle",
    "RWA_STOCK": "RWA / Tokenized Assets", "RWA": "RWA / Tokenized Assets",
    "DERIVATIVES": "Derivatives / Perps", "STAKING": "Staking / LST / LRT",
    "WALLET": "Wallet", "INFRA": "Dev Infrastructure",
}


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    source: str = ""


@dataclass
class Page:
    url: str
    text: str = ""
    raw: str = ""
    links: list[str] = field(default_factory=list)
    title: str = ""
    description: str = ""
    status: str = "NO_DATA"
    token_name: str = ""
    token_symbol: str = ""
    token_ca: str = ""


class SearchProvider:
    name = "search"
    def search(self, query: str, timeout: float) -> list[SearchHit]:
        raise NotImplementedError


class ConfiguredJsonSearchProvider(SearchProvider):
    """Optional replaceable JSON Search API (`HYPEREVM_SEARCH_API_URL`)."""
    name = "configured_search_api"
    def __init__(self, endpoint=None, api_key=None):
        self.endpoint = endpoint or os.getenv("HYPEREVM_SEARCH_API_URL", "")
        self.api_key = api_key or os.getenv("HYPEREVM_SEARCH_API_KEY", "")
    def search(self, query, timeout):
        if not self.endpoint:return []
        url=self.endpoint.format(query=quote_plus(query))
        headers={"Accept":"application/json"}
        if self.api_key:headers["Authorization"]=f"Bearer {self.api_key}"
        payload=requests.get(url,headers=headers,timeout=timeout).json()
        rows=payload.get("results") or payload.get("organic") or payload.get("web",{}).get("results") or []
        return [SearchHit(str(x.get("url") or x.get("link") or ""),str(x.get("title") or ""),str(x.get("snippet") or x.get("description") or ""),self.name) for x in rows if x.get("url") or x.get("link")]


class _Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.parts=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag.lower()=="a" and attrs.get("href"):self.links.append(attrs["href"])
    def handle_data(self,data):
        if data.strip():self.parts.append(data.strip())


class DuckDuckGoSearchProvider(SearchProvider):
    """Dependency-free fallback behind SearchProvider; not an identity authority."""
    name = "duckduckgo_html"
    endpoint = "https://html.duckduckgo.com/html/?q={query}"
    def search(self,query,timeout):
        response=requests.get(self.endpoint.format(query=quote_plus(query)),timeout=timeout,headers={"User-Agent":"Mozilla/5.0 HyperEVM-Radar/2.0"})
        response.raise_for_status();parser=_Links();parser.feed(response.text);hits=[]
        for raw in parser.links:
            if raw.startswith("//duckduckgo.com/l/?"):
                raw=parse_qs(urlparse("https:"+raw).query).get("uddg",[""])[0]
            if raw.startswith(("http://","https://")):hits.append(SearchHit(raw,source=self.name))
        return hits


class CompositeSearchProvider(SearchProvider):
    def __init__(self, providers=None):
        self.providers=providers or (ConfiguredJsonSearchProvider(),DuckDuckGoSearchProvider())
    def search(self,query,timeout):
        hits=[]
        for provider in self.providers:
            try:hits.extend(provider.search(query,timeout))
            except (requests.RequestException,ValueError,KeyError,TypeError):continue
        return hits


class PageProvider:
    def fetch(self, url: str, timeout: float) -> Page:
        raise NotImplementedError


class PublicPageProvider(PageProvider):
    def fetch(self,url,timeout):
        try:
            response=requests.get(url,timeout=timeout,allow_redirects=True,headers={"User-Agent":"Mozilla/5.0 HyperEVM-Radar/2.0"})
            response.raise_for_status();raw=response.text;parser=_Links();parser.feed(raw)
            links=list(dict.fromkeys(urljoin(response.url,x) for x in parser.links if x))
            text=" ".join(parser.parts);meta=META_RE.search(raw);title=TITLE_RE.search(raw)
            token_name,token_symbol,token_ca=_page_token_metadata(raw)
            return Page(str(response.url),text,raw,links,html.unescape(title.group(1)).strip() if title else "",html.unescape(meta.group(1)).strip() if meta else "","AVAILABLE",token_name,token_symbol,token_ca)
        except (requests.RequestException,ValueError):return Page(url,status="NO_DATA")


class XContentProvider:
    """Replaceable X reader; the public-page implementation may legitimately return NO_DATA."""
    def fetch(self, url: str, timeout: float) -> Page:
        raise NotImplementedError


class PublicXPageProvider(XContentProvider):
    def __init__(self,page_provider=None):self.pages=page_provider or PublicPageProvider()
    def fetch(self,url,timeout):return self.pages.fetch(url,timeout)


def _host(url):return (urlparse(url).hostname or "").lower().removeprefix("www.")
def _same_site(a,b):return bool(_host(a) and _host(a)==_host(b))
_COUNTRY_SECOND_LEVEL={"co","com","net","org","gov","ac","edu"}
def domain_parts(url):
    """Return hostname, registrable-domain approximation and subdomain generically."""
    hostname=_host(url);labels=[x for x in hostname.split(".") if x]
    width=3 if len(labels)>=3 and len(labels[-1])==2 and labels[-2] in _COUNTRY_SECOND_LEVEL else 2
    root=".".join(labels[-width:]) if len(labels)>=width else hostname
    subdomain=".".join(labels[:-width]) if len(labels)>width else ""
    return hostname,root,subdomain
def extract_core_word(url, title=""):
    """Extract a project clue from the registrable label without assuming a brand."""
    _,root,_=domain_parts(url)
    label=root.split(".")[0] if root else ""
    segments=[x.lower() for x in re.findall(r"[A-Za-z0-9]+",label) if x]
    if not segments:return ""
    # The full registrable label is the only deterministic URL-owned identity
    # clue. Title words are deliberately not allowed to replace it.
    return "".join(segments)
def derive_core_words(core_word, source_label=""):
    """Return the ordered 3/4/5-character prefixes available in the core word."""
    core="".join(re.findall(r"[a-z0-9]",str(core_word or "").lower()))
    return [core[:length] for length in (3,4,5) if len(core)>=length]
def _identity_words(*values):
    generic={"official","home","app","docs","protocol","finance","hyperliquid","hyperevm","the","and","website"}
    return {x for x in re.findall(r"[a-z0-9]{3,}"," ".join(str(v or "").lower() for v in values)) if x not in generic}
def _page_token_metadata(raw):
    """Read structured token records exposed by a public page, without prose gates."""
    def records(value):
        if isinstance(value,dict):
            yield value
            for child in value.values():yield from records(child)
        elif isinstance(value,list):
            for child in value:yield from records(child)
    for script in JSON_SCRIPT_RE.findall(str(raw or "")):
        try:value=json.loads(html.unescape(script).strip())
        except (TypeError,ValueError):continue
        for item in records(value):
            symbol=str(item.get("symbol") or "").strip()
            address=str(item.get("address") or item.get("contractAddress") or item.get("contract_address") or "").strip().lower()
            if symbol and ADDRESS_RE.fullmatch(address):
                return str(item.get("name") or "").strip(),symbol,address
    address=ADDRESS_RE.search(str(raw or ""))
    if address:return "","",address.group(0).lower()
    return "","",""
def _addresses(family):
    return {str(x).lower() for x in [family.get("creator"),*(family.get("member_addresses") or [])] if ADDRESS_RE.fullmatch(str(x or ""))}
def _project_addresses(page, evidence_source):
    if not page or page.status!="AVAILABLE":return []
    blob=page.raw+" "+page.text;out=[]
    role_words=("factory","router","vault","oracle","pool")
    for match in ADDRESS_RE.finditer(blob):
        context=blob[max(0,match.start()-120):match.end()+120].lower()
        role=next((word.upper() for word in role_words if word in context),"")
        out.append({"address":match.group(0).lower(),"role":role,"source_url":page.url,"evidence_source":evidence_source})
    return out


def discover_official_token(official_website,core_word,derived_words,sources=(),candidate_names=(),candidate_symbols=(),**_legacy):
    """Separate token candidate discovery from official-source CA confirmation."""
    if not official_website:
        return {"core_word":core_word,"derived_words":list(derived_words or []),"discovered_token_name":"","discovered_token_symbol":"","discovered_ca":"","official_token_name":"","official_token_symbol":"","official_token_ca":"","token_verification_status":"NO_DATA","token_evidence":[],"token_source_urls":[]}
    clues={str(core_word or "").lower(),*(str(x).lower() for x in derived_words or [])};clues.discard("")
    discovered_name=next((str(x) for x in candidate_names if _identity_words(x)&clues),"")
    discovered_symbol=next((str(x) for x in candidate_symbols if re.sub(r"[^a-z0-9]","",str(x).lower()) in clues),"")
    discovered_ca="";candidate_evidence=[];verified_evidence=[];source_urls=[];matches=[]
    for source in sources:
        ca=str(source.get("token_ca") or "").strip().lower()
        if not ADDRESS_RE.fullmatch(ca):continue
        url=str(source.get("url") or "");kind=str(source.get("kind") or "unknown")
        symbol=str(source.get("token_symbol") or "")
        item={"source":kind,"url":url,"ca":ca,"symbol":symbol}
        matches.append((source,item))
    for source,item in sorted(matches,key=lambda match:bool(match[1]["symbol"]),reverse=True):
        discovered_symbol=discovered_symbol or item["symbol"];discovered_ca=item["ca"]
        candidate_evidence.append(item)
        kind=item["source"];url=item["url"]
        if official_website and source.get("trusted") and kind in {"website","official_docs","official_x"}:
            verified_evidence.append(item);source_urls.append(url);break
    status="VERIFIED" if verified_evidence else "PARTIAL" if discovered_name or discovered_symbol or discovered_ca else "NO_DATA"
    return {"core_word":core_word,"derived_words":list(derived_words or []),"discovered_token_name":discovered_name,
        "discovered_token_symbol":discovered_symbol,"discovered_ca":discovered_ca,
        "official_token_name":discovered_name if verified_evidence else "","official_token_symbol":discovered_symbol if verified_evidence else "",
        "official_token_ca":discovered_ca if verified_evidence else "","token_verification_status":status,
        "token_evidence":verified_evidence or candidate_evidence,"token_source_urls":list(dict.fromkeys(source_urls))}


class FamilyIntelligenceEngine:
    def __init__(self,search=None,pages=None,x_provider=None,request_timeout=None,total_timeout=None,max_hits=12):
        self.search=search or CompositeSearchProvider();self.pages=pages or PublicPageProvider()
        self.x=x_provider or PublicXPageProvider(self.pages)
        self.request_timeout=float(request_timeout or os.getenv("IDENTITY_REQUEST_TIMEOUT","6"))
        self.total_timeout=float(total_timeout or os.getenv("IDENTITY_TOTAL_TIMEOUT","35"));self.max_hits=max_hits

    def enrich(self,family,auxiliary_candidates=()):
        started=time.monotonic();addresses=_addresses(family);evidence=[];hits=[]
        # Names and brand are optional extra queries; addresses always drive discovery first.
        clues=list(addresses)[:5]
        clues += [x for x in [family.get("brand_hint"),*(family.get("token_names") or []),*(family.get("token_symbols") or [])] if str(x or "").strip()][:2]
        for clue in clues:
            if time.monotonic()-started>=self.total_timeout:break
            try:hits.extend(self.search.search(f'"{clue}" HyperEVM official',self.request_timeout))
            except Exception as exc:evidence.append({"source":"search","status":"NO_DATA","detail":type(exc).__name__})
        for item in auxiliary_candidates:
            if item.get("url"):hits.append(SearchHit(item["url"],str(item.get("name") or ""),source=str(item.get("source") or "auxiliary")))
        for url in family.get("identity_urls") or []:
            if url:hits.append(SearchHit(str(url),source="IDENTITY_URL_SEED"))
        def unique_hits(values):
            found={};
            for hit in values:
                if hit.url:found.setdefault(hit.url,hit)
            return list(found.values())
        unique=unique_hits(hits);x_urls=[];web_hits=[]
        excluded={"github.com","t.me","telegram.me","youtube.com","x.com","twitter.com"}
        for hit in unique:
            match=X_RE.search(hit.url)
            if match:x_urls.append(match.group(0));evidence.append({"source":hit.source,"kind":"X_CANDIDATE","url":match.group(0)})
            elif _host(hit.url) not in excluded:web_hits.append(hit)
        website_page=None;website_candidate=web_hits[0].url if web_hits else "";fetched={}
        for hit in web_hits[:self.max_hits]:
            if time.monotonic()-started>=self.total_timeout:break
            try:page=self.pages.fetch(hit.url,self.request_timeout)
            except Exception as exc:
                evidence.append({"source":"website","url":hit.url,"status":"NO_DATA","detail":type(exc).__name__});continue
            if page.status=="AVAILABLE":
                fetched[page.url]=page
                page_blob=(page.raw+" "+page.text).lower()
                page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(page_blob)})
                if page.address_match or any(X_RE.search(link) for link in page.links):website_page=page;break
                if website_page is None:website_page=page
        # Domain-first runs after any website candidate and expands discovery; it
        # never confirms identity by the hostname/title alone.
        hostname=root_domain=subdomain=core_word="";derived_words=[];domain_hits=[]
        if website_page:
            hostname,root_domain,subdomain=domain_parts(website_page.url)
            root_label=root_domain.split(".")[0] if root_domain else ""
            core_word=extract_core_word(website_page.url,website_page.title);derived_words=derive_core_words(core_word,root_label)
            domain_clues=list(dict.fromkeys(x for x in (hostname,root_domain,core_word,*derived_words,website_page.title) if x))
            evidence.append({"source":"domain","kind":"DOMAIN_FACTS","source_url":website_page.url,"hostname":hostname,"root_domain":root_domain,"subdomain":subdomain,"title":website_page.title,"meta_description":website_page.description,"core_word":core_word,"derived_words":derived_words})
            for clue in domain_clues:
                if time.monotonic()-started>=self.total_timeout:break
                try:domain_hits.extend(self.search.search(f'"{clue}" X docs GitHub HyperEVM Hyperliquid',self.request_timeout))
                except Exception as exc:evidence.append({"source":"domain_search","status":"NO_DATA","detail":type(exc).__name__})
        unique=unique_hits(unique+domain_hits)
        for hit in domain_hits:
            match=X_RE.search(hit.url)
            if match:x_urls.append(match.group(0));evidence.append({"source":hit.source or "domain_search","kind":"X_CANDIDATE","url":match.group(0)})
        site_x=[]
        if website_page:
            site_x=[m.group(0) for link in website_page.links for m in [X_RE.search(link)] if m]
            x_urls=site_x+x_urls;evidence.append({"source":"website","url":website_page.url,"address_match":bool(getattr(website_page,"address_match",False)),"linked_x":site_x})
        x_url=next(iter(dict.fromkeys(x_urls)),"");x_page=Page(x_url,status="NO_DATA")
        if x_url and time.monotonic()-started<self.total_timeout:
            try:x_page=self.x.fetch(x_url,self.request_timeout)
            except Exception as exc:x_page=Page(x_url,status="NO_DATA");evidence.append({"source":"x_public","status":"NO_DATA","detail":type(exc).__name__})
            evidence.append({"source":"x_public","url":x_url,"status":x_page.status})
            if x_page.status=="AVAILABLE":
                evidence.append({"source":"x_public","kind":"PUBLIC_PROFILE_CONTENT","bio":x_page.description[:500],"page_excerpt":x_page.text[:700],"note":"pinned/recent post boundaries are retained only when the provider exposes reliable public content"})
        # X profile metadata can reveal the website even when search did not.
        if not website_page and x_page.status=="AVAILABLE":
            backlink=next((link for link in x_page.links if _host(link) not in {"x.com","twitter.com","t.co"}),"")
            if backlink:
                try:
                    page=self.pages.fetch(backlink,self.request_timeout)
                    if page.status=="AVAILABLE":
                        blob=(page.raw+" "+page.text).lower();page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(blob)})
                        website_page=page;website_candidate=page.url
                except Exception as exc:evidence.append({"source":"x_website","status":"NO_DATA","detail":type(exc).__name__})
        if website_page and not hostname:
            hostname,root_domain,subdomain=domain_parts(website_page.url)
            root_label=root_domain.split(".")[0] if root_domain else ""
            core_word=extract_core_word(website_page.url,website_page.title);derived_words=derive_core_words(core_word,root_label)
            site_x=[m.group(0) for link in website_page.links for m in [X_RE.search(link)] if m]
            evidence.append({"source":"domain","kind":"DOMAIN_FACTS","source_url":website_page.url,"hostname":hostname,"root_domain":root_domain,"subdomain":subdomain,"title":website_page.title,"meta_description":website_page.description,"core_word":core_word,"derived_words":derived_words})
            for clue in dict.fromkeys(x for x in (hostname,root_domain,core_word,*derived_words,website_page.title) if x):
                if time.monotonic()-started>=self.total_timeout:break
                try:domain_hits.extend(self.search.search(f'"{clue}" X docs GitHub HyperEVM Hyperliquid',self.request_timeout))
                except Exception as exc:evidence.append({"source":"domain_search","status":"NO_DATA","detail":type(exc).__name__})
            unique=unique_hits(unique+domain_hits)
        website=website_page.url if website_page else website_candidate
        x_links_site=bool(website and any(_same_site(link,website) for link in x_page.links))
        site_links_x=bool(website_page and site_x and x_url in site_x)
        homepage_address=bool(website_page and getattr(website_page,"address_match",False))
        # website -> docs and domain -> docs are followed even when the homepage
        # has no address. Same-root docs or docs directly linked by the site are trusted candidates.
        all_links=[hit.url for hit in unique]+(website_page.links if website_page else [])+(x_page.links if x_page else [])
        docs=list(dict.fromkeys(link for link in all_links if "docs" in _host(link) or "/docs" in link))
        github=list(dict.fromkeys(link for link in all_links if _host(link)=="github.com"))
        trusted_docs=[]
        for link in docs[:4]:
            linked_by_site=bool(website_page and link in website_page.links)
            same_root=bool(root_domain and domain_parts(link)[1]==root_domain)
            if not (linked_by_site or same_root):continue
            try:page=self.pages.fetch(link,self.request_timeout)
            except Exception as exc:evidence.append({"source":"official_docs","url":link,"status":"NO_DATA","detail":type(exc).__name__});continue
            if page.status=="AVAILABLE":
                blob=(page.raw+" "+page.text).lower();page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(blob)})
                trusted_docs.append(page);evidence.append({"source":"official_docs","url":page.url,"address_match":page.address_match})
        docs_address=any(getattr(page,"address_match",False) for page in trusted_docs)
        site_identity=_identity_words(root_domain,website_page.title if website_page else "",website_page.description if website_page else "")
        x_identity=_identity_words(x_page.title,x_page.description,x_page.text[:800])
        brand_consistent=bool(site_identity & x_identity)
        product_context=any(word in f" {(' '.join([website_page.text if website_page else '',x_page.text,*[p.text for p in trusted_docs]])).lower()} " for words in TYPE_RULES.values() for word in words)
        chain_context=any(word in (" ".join([website_page.text if website_page else "",x_page.text,*[p.text for p in trusted_docs]])).lower() for word in ("hyperevm","hyper evm","hyperliquid","chain 999"))
        if homepage_address or docs_address:verification="VERIFIED";verification_method="VERIFIED_BY_ADDRESS"
        elif site_links_x and x_links_site and brand_consistent and (chain_context or product_context):verification="VERIFIED";verification_method="VERIFIED_BY_CROSS_LINK"
        elif website or x_url:verification="PARTIAL"
        else:verification="NO_DATA"
        if verification!="VERIFIED":verification_method=verification
        # Do not put an unrelated search-result X in a verified alert. A lone X
        # candidate is retained only while the complete identity is PARTIAL.
        official_x=x_url if verification=="PARTIAL" or site_links_x or x_links_site else ""
        evidence.append({"kind":"CROSS_VERIFICATION","website_to_x":site_links_x,"x_to_website":x_links_site,"brand_consistent":brand_consistent,"chain_context":chain_context,"product_context":product_context,"family_address_on_website":homepage_address,"family_address_in_docs":docs_address,"status":verification_method})
        corpus=" ".join([family.get("platform_types") or "",website_page.text if website_page else "",website_page.description if website_page else "",x_page.text,x_page.description,*[p.text for p in trusted_docs]]).lower()
        padded=f" {corpus} ";types=[]
        roles=[x.strip().upper() for x in str(family.get("platform_types") or "").split(",") if x.strip()]
        types.extend(ROLE_TYPES[x] for x in roles if x in ROLE_TYPES)
        for label,words in TYPE_RULES.items():
            if any(word in padded for word in words):types.append(label)
        types=list(dict.fromkeys(types)) or ["Other"]
        title=(website_page.title if website_page else "") or x_page.title
        project_name=re.sub(r"\s*[-|].*$","",title).strip()[:120]
        description=((website_page.description if website_page else "") or x_page.description or (website_page.text[:400] if website_page else "")).strip()
        if docs:evidence.append({"source":"cross_links","kind":"DOCS","urls":list(dict.fromkeys(docs))})
        if github:evidence.append({"source":"cross_links","kind":"GITHUB","urls":list(dict.fromkeys(github))})
        token_pages=[];token_hits=[]
        token_clues=list(dict.fromkeys(x for x in (core_word,*derived_words) if x))
        token_targets=[f"site:{root_domain}"] if root_domain else []
        if x_url:token_targets.append(f"site:{_host(x_url)}/{urlparse(x_url).path.strip('/').split('/')[0]}")
        for clue in token_clues:
            for target in token_targets:
                if time.monotonic()-started>=self.total_timeout:break
                try:token_hits.extend(self.search.search(f'"{clue}" {target}',self.request_timeout))
                except Exception as exc:evidence.append({"source":"token_search","status":"NO_DATA","detail":type(exc).__name__})
        for hit in unique_hits(token_hits)[:self.max_hits]:
            if time.monotonic()-started>=self.total_timeout:break
            is_x=_host(hit.url) in {"x.com","twitter.com"}
            same_official_x=bool(is_x and x_url and urlparse(hit.url).path.strip("/").split("/")[0].lower()==urlparse(x_url).path.strip("/").split("/")[0].lower())
            if not (root_domain and domain_parts(hit.url)[1]==root_domain) and not same_official_x:continue
            try:page=(self.x if is_x else self.pages).fetch(hit.url,self.request_timeout)
            except Exception as exc:evidence.append({"source":"token_search","url":hit.url,"status":"NO_DATA","detail":type(exc).__name__});continue
            if page.status=="AVAILABLE":token_pages.append((page,"official_x" if is_x else "official_docs" if "docs" in _host(page.url) or "/docs" in page.url else "website"))
        if token_hits:evidence.append({"source":"token_search","kind":"TOKEN_CA_DISCOVERY","clues":token_clues,"urls":[page.url for page,_ in token_pages]})
        # Confirmation consumes Token/CA candidates supplied by established official sources.
        token_sources=[]
        if website_page:token_sources.append({"kind":"website","url":website_page.url,"text":website_page.raw+" "+website_page.text,"token_name":website_page.token_name,"token_symbol":website_page.token_symbol,"token_ca":website_page.token_ca,"trusted":True})
        if x_page.status=="AVAILABLE":token_sources.append({"kind":"official_x","url":x_page.url,"text":x_page.raw+" "+x_page.text,"token_name":x_page.token_name,"token_symbol":x_page.token_symbol,"token_ca":x_page.token_ca,"trusted":site_links_x or x_links_site})
        token_sources.extend({"kind":"official_docs","url":page.url,"text":page.raw+" "+page.text,"token_name":page.token_name,"token_symbol":page.token_symbol,"token_ca":page.token_ca,"trusted":True} for page in trusted_docs)
        token_sources.extend({"kind":kind,"url":page.url,"text":page.raw+" "+page.text,"token_name":page.token_name,"token_symbol":page.token_symbol,"token_ca":page.token_ca,"trusted":kind!="official_x" or site_links_x or x_links_site} for page,kind in token_pages)
        token_result=discover_official_token(website,core_word,derived_words,token_sources,family.get("token_names") or [],family.get("token_symbols") or [])
        token_ca=token_result["official_token_ca"];token_symbol=token_result["official_token_symbol"]
        token_status="CONFIRMED_OFFICIAL" if token_result["token_verification_status"]=="VERIFIED" else "NONE"
        discovered_token_name=token_result["discovered_token_name"];discovered_token_symbol=token_result["discovered_token_symbol"];discovered_ca=token_result["discovered_ca"]
        token_evidence_source=token_result["token_evidence"][0]["source"] if token_result["token_evidence"] else ""
        evidence.extend({**item,"kind":"OFFICIAL_TOKEN_CA" if token_status=="CONFIRMED_OFFICIAL" else "TOKEN_CANDIDATE"} for item in token_result["token_evidence"])
        if token_status=="NONE":evidence.append({"kind":"OFFICIAL_TOKEN","status":"NONE","detail":"no official token association found"})
        evidence.append({"kind":"INFRASTRUCTURE_CLASSIFICATION","types":types,"role_inputs":roles})
        discovered_project_addresses=[]
        for page,source in [(website_page,"website"),(x_page,"x_public")]+[(page,"official_docs") for page in trusted_docs]:
            for item in _project_addresses(page,source):
                if (item["address"],item["source_url"]) not in {(x["address"],x["source_url"]) for x in discovered_project_addresses}:discovered_project_addresses.append(item)
        return {
            "family_id":family.get("id"),"subject_key":family.get("subject_key") or (f"family:{family.get('id')}" if family.get("id") else ""),"project_name":project_name,"official_x":official_x,
            "official_website":website,"description":description[:700],"infrastructure_types":types,
            "official_token_symbol":token_symbol,"official_token_ca":token_ca,"token_status":token_status,
            "source_url":website,"hostname":hostname,"root_domain":root_domain,"subdomain":subdomain,"core_word":core_word,"derived_words":derived_words,
            "discovered_token_name":discovered_token_name,"discovered_token_symbol":discovered_token_symbol,"discovered_ca":discovered_ca,"evidence_source":token_evidence_source,
            "token_verification_status":token_result["token_verification_status"],"token_evidence":token_result["token_evidence"],"token_source_urls":token_result["token_source_urls"],
            "verification_method":verification_method,
            "verification_status":verification,"confidence":90 if verification=="VERIFIED" else 55 if verification=="PARTIAL" else 0,
            "docs":list(dict.fromkeys(docs)),"github":list(dict.fromkeys(github)),"discovered_project_addresses":discovered_project_addresses,"discovered_candidates":len(unique),"evidence":evidence,
        }
