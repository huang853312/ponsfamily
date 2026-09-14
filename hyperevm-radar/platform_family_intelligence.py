"""Address-first identity enrichment for HyperEVM Platform Families.

Identity discovery is intentionally conservative: search engines only surface candidates;
project verification still requires on-site/on-doc address evidence or strong official-link
cross checks. The module performs no database writes.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests

ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
X_RE = re.compile(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})(?:[^\s\"'<>]*)?", re.I)
META_RE = re.compile(r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)', re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
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


class IdentityBudgetExceeded(TimeoutError):
    pass


class SearchUnavailable(RuntimeError):
    pass

class ConfiguredJsonSearchProvider(SearchProvider):
    """Configured API, with explicit LangSearch request/response adaptation."""
    name = "configured_search_api"
    def __init__(self, endpoint=None, api_key=None):
        self.endpoint = endpoint or os.getenv("HYPEREVM_SEARCH_API_URL", "")
        self.api_key = api_key or os.getenv("HYPEREVM_SEARCH_API_KEY", "")
    @staticmethod
    def _hits(rows, source):
        if not isinstance(rows, list):
            raise ValueError("Search API result list is invalid")
        return [SearchHit(str(x.get("url") or x.get("link")), str(x.get("title") or x.get("name") or ""),
            str(x.get("snippet") or x.get("description") or " ".join(x.get("snippets") or []) or ""), source)
            for x in rows if isinstance(x, dict) and (x.get("url") or x.get("link"))]
    def search(self, query, timeout):
        if not self.endpoint:
            return []
        headers = {"Accept": "application/json", "User-Agent": "HyperEVM-Radar/3.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if urlparse(self.endpoint).hostname == "api.langsearch.com":
            response = requests.post(self.endpoint, headers=headers,
                json={"query": query, "count": 5, "freshness": "noLimit", "summary": False}, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            rows = (payload.get("data") or {}).get("webPages", {}).get("value") or []
        else:
            response = requests.get(self.endpoint.format(query=quote_plus(query)), headers=headers, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("results") or payload.get("organic") or payload.get("web", {}).get("results") or []
        return self._hits(rows, self.name)

class YouSearchProvider(SearchProvider):
    name = "you_search_api"
    def __init__(self):
        self.api_key = os.getenv("YDC_API_KEY", "")
    def search(self, query, timeout):
        if not self.api_key:
            return []
        response = requests.post("https://ydc-index.io/v1/search", headers={"X-API-Key": self.api_key},
            json={"query": query, "count": 5}, timeout=timeout)
        response.raise_for_status()
        return ConfiguredJsonSearchProvider._hits((response.json().get("results") or {}).get("web") or [], self.name)

class ExaSearchProvider(SearchProvider):
    name = "exa_search_api"
    def __init__(self):
        self.api_key = os.getenv("EXA_API_KEY", "")
    def search(self, query, timeout):
        if not self.api_key:
            return []
        response = requests.post("https://api.exa.ai/search", headers={"x-api-key": self.api_key},
            json={"query": query, "numResults": 5}, timeout=timeout)
        response.raise_for_status()
        return ConfiguredJsonSearchProvider._hits(response.json().get("results") or [], self.name)

class TinyFishSearchProvider(SearchProvider):
    name = "tinyfish_search_api"
    def __init__(self):
        self.api_key = os.getenv("TINYFISH_API_KEY", "")
    def search(self, query, timeout):
        if not self.api_key:
            return []
        response = requests.get("https://api.search.tinyfish.ai", headers={"X-API-Key": self.api_key},
            params={"q": query, "query": query}, timeout=timeout)
        response.raise_for_status()
        return ConfiguredJsonSearchProvider._hits(response.json().get("results") or [], self.name)



class _Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.parts=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag.lower()=="a" and attrs.get("href"):
            self.links.append(attrs["href"])
    def handle_data(self, data):
        if data.strip():
            self.parts.append(data.strip())

class _SearchResultsParser(HTMLParser):
    """Small parser that keeps anchor text as a useful title/snippet hint."""
    def __init__(self):
        super().__init__(); self.rows=[]; self._href=""; self._parts=[]
    def handle_starttag(self, tag, attrs):
        if tag.lower()=="a":
            href=dict(attrs).get("href", "")
            if href:
                self._href=href; self._parts=[]
    def handle_data(self, data):
        if self._href and data.strip():
            self._parts.append(data.strip())
    def handle_endtag(self, tag):
        if tag.lower()=="a" and self._href:
            self.rows.append((self._href, " ".join(self._parts).strip()))
            self._href=""; self._parts=[]

class DuckDuckGoSearchProvider(SearchProvider):
    """No-key public fallback using DDG HTML and Lite endpoints."""
    name = "duckduckgo"
    endpoints = (
        "https://html.duckduckgo.com/html/?q={query}",
        "https://lite.duckduckgo.com/lite/?q={query}",
    )
    @staticmethod
    def _unwrap(raw):
        if raw.startswith("//"):
            raw="https:"+raw
        parsed=urlparse(raw)
        if parsed.hostname and "duckduckgo.com" in parsed.hostname and parsed.path.startswith("/l/"):
            raw=parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(raw)
    def search(self, query, timeout):
        headers={"User-Agent":"Mozilla/5.0 (compatible; HyperEVM-Radar/3.0)", "Accept-Language":"en-US,en;q=0.8"}
        last_error=None
        deadline=time.monotonic()+timeout
        for endpoint in self.endpoints:
            remaining=deadline-time.monotonic()
            if remaining<=0: break
            try:
                response=requests.get(endpoint.format(query=quote_plus(query)),timeout=remaining,headers=headers)
                response.raise_for_status()
                parser=_SearchResultsParser(); parser.feed(response.text)
                hits=[]; seen=set()
                for raw,title in parser.rows:
                    url=self._unwrap(raw)
                    if not url.startswith(("http://","https://")):
                        continue
                    host=(urlparse(url).hostname or "").lower()
                    if not host or "duckduckgo.com" in host:
                        continue
                    key=url.split("#",1)[0]
                    if key in seen:
                        continue
                    seen.add(key); hits.append(SearchHit(url,title,"",self.name))
                if hits:
                    return hits
            except (requests.RequestException, ValueError) as exc:
                last_error=exc
        if last_error:
            raise last_error
        return []

class BingSearchProvider(SearchProvider):
    """Second no-key fallback. Search output is discovery evidence only, never authority."""
    name = "bing_html"
    endpoint = "https://www.bing.com/search?q={query}&count=12"
    def search(self, query, timeout):
        response=requests.get(
            self.endpoint.format(query=quote_plus(query)), timeout=timeout,
            headers={"User-Agent":"Mozilla/5.0 (compatible; HyperEVM-Radar/3.0)","Accept-Language":"en-US,en;q=0.8"},
        )
        response.raise_for_status()
        parser=_SearchResultsParser(); parser.feed(response.text)
        hits=[]; seen=set()
        excluded={"bing.com","microsoft.com","go.microsoft.com"}
        for raw,title in parser.rows:
            if not raw.startswith(("http://","https://")):
                continue
            host=(urlparse(raw).hostname or "").lower().removeprefix("www.")
            if not host or host in excluded:
                continue
            key=raw.split("#",1)[0]
            if key in seen:
                continue
            seen.add(key); hits.append(SearchHit(raw,title,"",self.name))
        return hits

class CompositeSearchProvider(SearchProvider):
    def __init__(self, providers=None):
        self.providers=providers or (ConfiguredJsonSearchProvider(), YouSearchProvider(), ExaSearchProvider(), TinyFishSearchProvider(), DuckDuckGoSearchProvider(), BingSearchProvider())
    def search(self, query, timeout, deadline=None):
        hits=[]; seen=set()
        completed=0; failures=0
        targets={a.lower() for a in ADDRESS_RE.findall(query)}
        for provider in self.providers:
            if isinstance(provider,ConfiguredJsonSearchProvider) and not provider.endpoint: continue
            if isinstance(provider,(YouSearchProvider,ExaSearchProvider,TinyFishSearchProvider)) and not provider.api_key: continue
            remaining=timeout if deadline is None else min(timeout,deadline-time.monotonic())
            if remaining<=0: raise IdentityBudgetExceeded('search_fallback_budget_exhausted')
            try:
                rows=provider.search(query, remaining)
                completed+=1
            except (requests.RequestException, ValueError, KeyError, TypeError):
                failures+=1
                continue
            # Address queries require address evidence in the returned metadata.
            # Nonempty unrelated results must not prevent fallback providers.
            if targets:
                rows=[h for h in rows if any(a in (h.url+" "+h.title+" "+h.snippet).lower() for a in targets)]
            for hit in rows:
                key=hit.url.split("#",1)[0]
                if key and key not in seen:
                    seen.add(key); hits.append(hit)
            # A configured provider or a successful public provider is enough; avoid
            # needlessly hitting every engine and burning the identity time budget.
            if rows:
                break
        if failures and not completed:
            raise SearchUnavailable('all_search_providers_failed')
        return hits


class PageProvider:
    def fetch(self, url: str, timeout: float) -> Page:
        raise NotImplementedError

class PublicPageProvider(PageProvider):
    def fetch(self, url, timeout):
        try:
            response=requests.get(url,timeout=timeout,allow_redirects=True,headers={"User-Agent":"Mozilla/5.0 (compatible; HyperEVM-Radar/3.0)"})
            response.raise_for_status(); raw=response.text; parser=_Links(); parser.feed(raw)
            links=list(dict.fromkeys(urljoin(response.url,x) for x in parser.links if x))
            text=" ".join(parser.parts); meta=META_RE.search(raw); title=TITLE_RE.search(raw)
            token_name,token_symbol,token_ca=_page_token_metadata(raw)
            return Page(str(response.url),text,raw,links,html.unescape(title.group(1)).strip() if title else "",html.unescape(meta.group(1)).strip() if meta else "","AVAILABLE",token_name,token_symbol,token_ca)
        except (requests.RequestException, ValueError):
            return Page(url,status="NO_DATA")

class XContentProvider:
    def fetch(self, url: str, timeout: float) -> Page:
        raise NotImplementedError

class PublicXPageProvider(XContentProvider):
    def __init__(self,page_provider=None): self.pages=page_provider or PublicPageProvider()
    def fetch(self,url,timeout): return self.pages.fetch(url,timeout)

def _host(url): return (urlparse(url).hostname or "").lower().removeprefix("www.")
def _same_site(a,b): return bool(_host(a) and _host(a)==_host(b))

# Shared documentation infrastructure is useful discovery evidence, but it is not
# the investigated project itself. Keep this deliberately narrow to the confirmed
# GitBook false-positive class.
SHARED_IDENTITY_HOSTS={"gitbook.com","gitbook.io"}
SHARED_IDENTITY_X_HANDLES={"gitbookio"}

def _is_shared_identity_host(url):
    host=_host(url)
    return bool(host and (host in SHARED_IDENTITY_HOSTS or host.endswith(".gitbook.io")))

def _is_shared_provider_x(url):
    match=X_RE.search(str(url or ""))
    return bool(match and match.group(1).lower() in SHARED_IDENTITY_X_HANDLES)
_COUNTRY_SECOND_LEVEL={"co","com","net","org","gov","ac","edu"}
def domain_parts(url):
    hostname=_host(url); labels=[x for x in hostname.split(".") if x]
    width=3 if len(labels)>=3 and len(labels[-1])==2 and labels[-2] in _COUNTRY_SECOND_LEVEL else 2
    root=".".join(labels[-width:]) if len(labels)>=width else hostname
    subdomain=".".join(labels[:-width]) if len(labels)>width else ""
    return hostname,root,subdomain

def extract_core_word(url, title=""):
    if _is_shared_identity_host(url): return ""
    _,root,_=domain_parts(url); label=root.split(".")[0] if root else ""
    return "".join(x.lower() for x in re.findall(r"[A-Za-z0-9]+",label) if x)

def derive_core_words(core_word, source_label=""):
    core="".join(re.findall(r"[a-z0-9]",str(core_word or "").lower()))
    return [core[:length] for length in (3,4,5) if len(core)>=length]

def _identity_words(*values):
    generic={"official","home","app","docs","protocol","finance","hyperliquid","hyperevm","the","and","website"}
    return {x for x in re.findall(r"[a-z0-9]{3,}"," ".join(str(v or "").lower() for v in values)) if x not in generic}

def _page_token_metadata(raw):
    def records(value):
        if isinstance(value,dict):
            yield value
            for child in value.values(): yield from records(child)
        elif isinstance(value,list):
            for child in value: yield from records(child)
    for script in JSON_SCRIPT_RE.findall(str(raw or "")):
        try: value=json.loads(html.unescape(script).strip())
        except (TypeError,ValueError): continue
        for item in records(value):
            symbol=str(item.get("symbol") or "").strip()
            address=str(item.get("address") or item.get("contractAddress") or item.get("contract_address") or "").strip().lower()
            if symbol and ADDRESS_RE.fullmatch(address):
                return str(item.get("name") or "").strip(),symbol,address
    address=ADDRESS_RE.search(str(raw or ""))
    return ("","",address.group(0).lower()) if address else ("","","")

def _ordered_addresses(family):
    values=[family.get("creator"),*(family.get("member_addresses") or [])]
    out=[]
    for value in values:
        value=str(value or "").lower()
        if ADDRESS_RE.fullmatch(value) and value not in out: out.append(value)
    return out

def _addresses(family): return set(_ordered_addresses(family))

def _project_addresses(page, evidence_source):
    if not page or page.status!="AVAILABLE": return []
    blob=page.raw+" "+page.text; out=[]; role_words=("factory","router","vault","oracle","pool")
    for match in ADDRESS_RE.finditer(blob):
        context=blob[max(0,match.start()-120):match.end()+120].lower()
        role=next((word.upper() for word in role_words if word in context),"")
        out.append({"address":match.group(0).lower(),"role":role,"source_url":page.url,"evidence_source":evidence_source})
    return out

PARKED_PAGE_MARKERS = (
    "domain for sale", "buy this domain", "this domain is for sale",
    "afternic", "sedo domain parking", "hugedomains", "parkingcrew",
)

def _is_docs_url(url):
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

TOKEN_DETAIL_MARKERS = ("token", "tokens", "contract", "contracts", "address", "addresses", "deployment", "deployments", "governance", "airdrop", "rewards")

def _looks_like_token_detail_url(url, root_domain=""):
    if not url.startswith(("http://","https://")): return False
    if root_domain and domain_parts(url)[1] != root_domain: return False
    parsed=urlparse(url); blob=((parsed.path or "")+" "+(parsed.query or "")).lower()
    return any(marker in blob for marker in TOKEN_DETAIL_MARKERS)

def _page_contains_address(page,address):
    if not page or page.status!="AVAILABLE" or not ADDRESS_RE.fullmatch(str(address or "")): return False
    return str(address).lower() in (page.raw+" "+page.text).lower()

def _contextual_token_sources(page, clues, kind, trusted=False, excluded_addresses=()):
    if not page or page.status!="AVAILABLE": return []
    blob=page.raw+" "+page.text; lower=blob.lower(); normalized_clues=[re.sub(r"[^a-z0-9]","",str(x).lower()) for x in clues if x]
    excluded={str(x).lower() for x in excluded_addresses or ()}
    out=[]
    for match in ADDRESS_RE.finditer(blob):
        ca=match.group(0).lower()
        # Platform Family contracts (Router/Vault/Factory/etc.) must never become a
        # platform-token CA merely because the project name appears nearby.
        if ca in excluded: continue
        context=lower[max(0,match.start()-220):match.end()+220]
        norm_context=re.sub(r"[^a-z0-9]","",context)
        clue=next((x for x in normalized_clues if x and x in norm_context),"")
        explicit=any(marker in context for marker in ("token address","token contract","contract address","ticker","symbol","official token","governance token"))
        if not clue and not explicit: continue
        out.append({"kind":kind,"url":page.url,"token_name":"","token_symbol":"","token_ca":ca,"trusted":bool(trusted),"context_clue":clue,"explicit_token_context":explicit})
    return out

def _is_parked_page(page):
    if not page or page.status!="AVAILABLE": return False
    blob=" ".join((page.title,page.description,page.text[:5000])).lower()
    return any(marker in blob for marker in PARKED_PAGE_MARKERS)

def discover_official_token(official_website,core_word,derived_words,sources=(),candidate_names=(),candidate_symbols=(),**_legacy):
    if not official_website:
        return {"core_word":core_word,"derived_words":list(derived_words or []),"discovered_token_name":"","discovered_token_symbol":"","discovered_ca":"","official_token_name":"","official_token_symbol":"","official_token_ca":"","token_verification_status":"NO_DATA","token_evidence":[],"token_source_urls":[]}
    clues={str(core_word or "").lower(),*(str(x).lower() for x in derived_words or [])}; clues.discard("")
    discovered_name=next((str(x) for x in candidate_names if _identity_words(x)&clues),"")
    discovered_symbol=next((str(x) for x in candidate_symbols if re.sub(r"[^a-z0-9]","",str(x).lower()) in clues),"")
    discovered_ca=""; candidate_evidence=[]; verified_evidence=[]; source_urls=[]; matches=[]
    for source in sources:
        ca=str(source.get("token_ca") or "").strip().lower()
        if not ADDRESS_RE.fullmatch(ca): continue
        item={"source":str(source.get("kind") or "unknown"),"url":str(source.get("url") or ""),"ca":ca,"symbol":str(source.get("token_symbol") or "")}
        matches.append((source,item))
    for source,item in sorted(matches,key=lambda m:bool(m[1]["symbol"]),reverse=True):
        discovered_symbol=discovered_symbol or item["symbol"]; discovered_ca=item["ca"]; candidate_evidence.append(item)
        if official_website and source.get("trusted") and item["source"] in {"website","official_docs","official_x"}:
            verified_evidence.append(item); source_urls.append(item["url"]); break
    status="VERIFIED" if verified_evidence else "PARTIAL" if discovered_name or discovered_symbol or discovered_ca else "NO_DATA"
    return {"core_word":core_word,"derived_words":list(derived_words or []),"discovered_token_name":discovered_name,"discovered_token_symbol":discovered_symbol,"discovered_ca":discovered_ca,"official_token_name":discovered_name if verified_evidence else "","official_token_symbol":discovered_symbol if verified_evidence else "","official_token_ca":discovered_ca if verified_evidence else "","token_verification_status":status,"token_evidence":verified_evidence or candidate_evidence,"token_source_urls":list(dict.fromkeys(source_urls))}

class FamilyIntelligenceEngine:
    def __init__(self,search=None,pages=None,x_provider=None,request_timeout=None,total_timeout=None,max_hits=12):
        self.search=search or CompositeSearchProvider(); self.pages=pages or PublicPageProvider(); self.x=x_provider or PublicXPageProvider(self.pages)
        self.request_timeout=float(request_timeout or os.getenv("IDENTITY_REQUEST_TIMEOUT","6")); self.total_timeout=float(total_timeout or os.getenv("IDENTITY_TOTAL_TIMEOUT","45")); self.max_hits=max_hits

    def _request_timeout(self, started):
        remaining=self.total_timeout-(time.monotonic()-started)
        if remaining<=0: raise IdentityBudgetExceeded('identity_budget_exhausted')
        return min(self.request_timeout,remaining)

    def _search(self, query, started, evidence):
        if time.monotonic()-started>=self.total_timeout:
            evidence.append({'source':'search','query':query[:180],'status':'SKIPPED','detail':'IdentityBudgetExceeded'})
            return []
        try:
            timeout=self._request_timeout(started)
            if isinstance(self.search,CompositeSearchProvider):
                rows=self.search.search(query,timeout,deadline=started+self.total_timeout)
            else:
                rows=self.search.search(query,timeout)
            evidence.append({"source":"search","query":query[:180],"status":"HITS" if rows else "NO_DATA","hits":len(rows)})
            return rows
        except Exception as exc:
            evidence.append({"source":"search","query":query[:180],"status":"ERROR","detail":type(exc).__name__}); return []

    def _direct_address_identity(self, address, started, evidence):
        """Return non-search-engine website/X candidates and contract-name clues."""
        hits=[]; clues=[]
        if time.monotonic()-started>=self.total_timeout: return hits,clues
        # DEX Screener exposes website/social metadata attached to a token/pair.
        # Treat it as discovery evidence only; normal cross-verification still applies.
        try:
            response=requests.get(
                f"https://api.dexscreener.com/token-pairs/v1/hyperevm/{address}",
                timeout=self._request_timeout(started),
                headers={"Accept":"application/json","User-Agent":"HyperEVM-Radar/3.1"},
            )
            response.raise_for_status(); rows=response.json()
            if not isinstance(rows,list): rows=[]
            for row in rows[:8]:
                info=row.get("info") or {}
                for item in info.get("websites") or []:
                    url=str((item or {}).get("url") or "").strip()
                    if url.startswith(("http://","https://")):
                        hits.append(SearchHit(url,source="DEXSCREENER_ADDRESS_METADATA"))
                for item in info.get("socials") or []:
                    item=item or {}; platform=str(item.get("platform") or "").lower(); handle=str(item.get("handle") or item.get("url") or "").strip()
                    if platform in {"twitter","x"} and handle:
                        if handle.startswith(("http://","https://")): url=handle
                        else: url="https://x.com/"+handle.lstrip("@/")
                        hits.append(SearchHit(url,source="DEXSCREENER_ADDRESS_METADATA"))
                for side in ("baseToken","quoteToken"):
                    token=row.get(side) or {}
                    if str(token.get("address") or "").lower()==address.lower():
                        for value in (token.get("name"),token.get("symbol")):
                            value=str(value or "").strip()
                            if len(value)>=3 and value.lower() not in {"token","unknown"}: clues.append(value)
            evidence.append({"source":"dexscreener_address_metadata","address":address,"status":"HITS" if hits else "NO_DATA","hits":len(hits)})
        except Exception as exc:
            evidence.append({"source":"dexscreener_address_metadata","address":address,"status":"NO_DATA","detail":type(exc).__name__})

        # Explorer pages are available immediately after deployment even when web
        # search has not indexed the address. A verified contract name becomes a
        # search clue, never an identity assertion.
        if time.monotonic()-started<self.total_timeout:
            try:
                page=self.pages.fetch(f"https://hyperevmscan.io/address/{address}",self._request_timeout(started))
                blob=" ".join((page.title,page.description,page.text[:12000])) if page.status=="AVAILABLE" else ""
                match=re.search(r"Contract Name\s+([A-Za-z][A-Za-z0-9_.$-]{2,80})",blob,re.I)
                name=match.group(1).strip() if match else ""
                generic=("proxy","erc20","token","contract","transparentupgradeableproxy","beaconproxy")
                if name and name.lower() not in generic and not name.lower().endswith("proxy"):
                    clues.append(name)
                evidence.append({"source":"hyperevmscan_address","address":address,"status":page.status,"contract_name":name[:100]})
            except Exception as exc:
                evidence.append({"source":"hyperevmscan_address","address":address,"status":"NO_DATA","detail":type(exc).__name__})
        return hits,list(dict.fromkeys(clues))

    def enrich(self,family,auxiliary_candidates=()):
        started=time.monotonic(); ordered_addresses=_ordered_addresses(family); addresses=set(ordered_addresses); evidence=[]; hits=[]; direct_clues=[]
        # First ask sources that know the address directly. This avoids waiting for
        # a public search engine to index a brand-new HyperEVM deployment.
        for address in ordered_addresses[:3]:
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
        # Docs discovery is broader than generic web identity discovery. Search the
        # creator plus the whole Family (bounded for latency), because official docs
        # often publish only one Router/Factory/Vault address and not the first three.
        for address in ordered_addresses[:12]:
            # Keep the original query shape for existing providers/tests, then add
            # the broader deployment/address vocabulary for deeper Docs discovery.
            hits.extend(self._search(f'"{address}" (docs OR documentation OR contracts)',started,evidence))
            hits.extend(self._search(
                f'"{address}" (deployments OR addresses OR HyperEVM OR Hyperliquid)',
                started,evidence,
            ))
            if time.monotonic()-started>=self.total_timeout: break
        for address in ordered_addresses[:6]:
            hits.extend(self._search(f'site:gitbook.io "{address}"',started,evidence))
            if time.monotonic()-started>=self.total_timeout: break
        text_clues=[]
        for x in direct_clues:
            x=str(x or "").strip()
            if len(x)>=3 and x.lower() not in {y.lower() for y in text_clues}: text_clues.append(x)
        for x in [family.get("brand_hint"),*(family.get("token_names") or []),*(family.get("token_symbols") or [])]:
            x=str(x or "").strip()
            if len(x)>=3 and x.lower() not in {y.lower() for y in text_clues}: text_clues.append(x)
        for clue in text_clues[:4]:
            hits.extend(self._search(f'"{clue}" HyperEVM',started,evidence))
            hits.extend(self._search(f'"{clue}" HyperEVM (official OR app OR protocol)',started,evidence))
            hits.extend(self._search(f'site:x.com "{clue}" HyperEVM',started,evidence))
            hits.extend(self._search(f'"{clue}" docs HyperEVM',started,evidence))
        for item in auxiliary_candidates:
            if item.get("url"): hits.append(SearchHit(item["url"],str(item.get("name") or ""),source=str(item.get("source") or "auxiliary")))
        for url in family.get("identity_urls") or []:
            if url: hits.append(SearchHit(str(url),source="IDENTITY_URL_SEED"))

        def unique_hits(values):
            found={}
            for hit in values:
                if hit.url: found.setdefault(hit.url.split("#",1)[0],hit)
            return list(found.values())

        def useful_docs(url):
            host=_host(url); path=(urlparse(url).path or "").lower()
            return (_is_docs_url(url) and host not in {"docs.github.com","docs.fivem.net","docs.google.com","support.github.com"}
                    and not any(part in path for part in ("/site-policy/","/privacy","/terms-of-service")))

        unique=unique_hits(hits); x_urls=[]; docs_hits=[]; web_hits=[]; github_hits=[]
        excluded={"github.com","githubstatus.com","www.githubstatus.com","docs.github.com","support.github.com","t.me","telegram.me","youtube.com","x.com","twitter.com","dexscreener.com","coingecko.com","coinmarketcap.com","defillama.com","debank.com","etherscan.io"}
        for hit in unique:
            match=X_RE.search(hit.url)
            if match:
                candidate_x=match.group(0)
                if _is_shared_provider_x(candidate_x):
                    evidence.append({"source":hit.source,"kind":"REJECTED_SHARED_PROVIDER_X","url":candidate_x,"title":hit.title[:160]})
                else:
                    x_urls.append(candidate_x); evidence.append({"source":hit.source,"kind":"X_CANDIDATE","url":candidate_x,"title":hit.title[:160]})
            elif useful_docs(hit.url):
                docs_hits.append(hit); evidence.append({"source":hit.source,"kind":"DOCS_CANDIDATE","url":hit.url,"title":hit.title[:160]})
            elif _host(hit.url)=="github.com":
                github_hits.append(hit); evidence.append({"source":hit.source,"kind":"GITHUB_CANDIDATE","url":hit.url,"title":hit.title[:160]})
            elif _is_shared_identity_host(hit.url):
                evidence.append({"source":hit.source,"kind":"REJECTED_SHARED_IDENTITY_HOST","url":hit.url,"title":hit.title[:160]})
            elif _host(hit.url) not in excluded:
                web_hits.append(hit)

        # GitHub is discovery-only. It may reveal an official website/X/Docs backlink,
        # but GitHub alone never upgrades a project to VERIFIED.
        for hit in github_hits[:4]:
            if time.monotonic()-started>=self.total_timeout: break
            try: page=self.pages.fetch(hit.url,self._request_timeout(started))
            except Exception as exc:
                evidence.append({"source":"github_discovery","url":hit.url,"status":"NO_DATA","detail":type(exc).__name__}); continue
            if page.status!="AVAILABLE": continue
            for link in page.links:
                match=X_RE.search(link)
                if match:
                    candidate_x=match.group(0)
                    if _is_shared_provider_x(candidate_x):
                        evidence.append({"source":"github_discovery","kind":"REJECTED_SHARED_PROVIDER_X","url":candidate_x})
                    else:
                        x_urls.append(candidate_x); evidence.append({"source":"github_discovery","kind":"X_CANDIDATE","url":candidate_x})
                elif useful_docs(link):
                    docs_hits.append(SearchHit(link,source="GITHUB_BACKLINK"))
                elif link.startswith(("http://","https://")) and _host(link) not in excluded:
                    web_hits.append(SearchHit(link,source="GITHUB_BACKLINK"))

        direct_docs_pages=[]
        seen_docs=set()
        docs_queue=list(dict.fromkeys(hit.url for hit in docs_hits if useful_docs(hit.url)))[:8]
        # Crawl one shallow layer of likely contract/address/deployment pages. This
        # turns a Docs landing-page hit into the exact page that contains Family CAs.
        while docs_queue and len(direct_docs_pages)<12:
            if time.monotonic()-started>=self.total_timeout: break
            docs_url=docs_queue.pop(0).split("#",1)[0]
            if not docs_url or docs_url in seen_docs: continue
            seen_docs.add(docs_url)
            try: page=self.pages.fetch(docs_url,self._request_timeout(started))
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
                    candidate_x=match.group(0)
                    if _is_shared_provider_x(candidate_x):
                        evidence.append({"source":"direct_docs","kind":"REJECTED_SHARED_PROVIDER_X","url":candidate_x})
                    else:
                        x_urls.append(candidate_x)
                        evidence.append({"source":"direct_docs","kind":"X_CANDIDATE","url":candidate_x})
                    continue
                if link.startswith(("http://","https://")) and _host(link) not in excluded and not _is_docs_url(link):
                    web_hits.append(SearchHit(link,source="DOCS_BACKLINK"))

        website_page=None; website_candidate=""; best_website_score=-1
        for hit in web_hits[:self.max_hits]:
            if time.monotonic()-started>=self.total_timeout: break
            try: page=self.pages.fetch(hit.url,self._request_timeout(started))
            except Exception as exc:
                evidence.append({"source":"website","url":hit.url,"status":"NO_DATA","detail":type(exc).__name__}); continue
            if page.status!="AVAILABLE": continue
            if _is_shared_identity_host(page.url):
                evidence.append({"source":"website","url":page.url,"status":"REJECTED_SHARED_IDENTITY_HOST"}); continue
            if _is_parked_page(page):
                evidence.append({"source":"website","url":page.url,"status":"REJECTED_PARKED"}); continue
            page_blob=(page.raw+" "+page.text).lower(); page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(page_blob)})
            # A social link alone never establishes relevance to this deployment.
            blob=" ".join((page.title,page.description,page.text)).lower()
            relevant_clue=any(len(c)>=3 and re.search(r"(?<!\w)"+re.escape(c.lower())+r"(?!\w)",blob) for c in text_clues)
            docs_address_link=any(getattr(d,"address_match",False) and
                (any(_same_site(link,page.url) for link in d.links) or
                 any(_same_docs_space(link,d.url) for link in page.links if _is_docs_url(link)))
                for d in direct_docs_pages)
            score=100 if page.address_match else 80 if docs_address_link else 60 if hit.source in {"DEXSCREENER_ADDRESS_METADATA","IDENTITY_URL_SEED"} else 40 if relevant_clue else -1
            if score<0:
                evidence.append({"source":"website","url":page.url,"status":"REJECTED_UNRELATED"}); continue
            if score>best_website_score:
                website_page=page; website_candidate=page.url; best_website_score=score
            if page.address_match: break

        hostname=root_domain=subdomain=core_word=""; derived_words=[]; domain_hits=[]
        if website_page:
            hostname,root_domain,subdomain=domain_parts(website_page.url); core_word=extract_core_word(website_page.url,website_page.title); derived_words=derive_core_words(core_word)
            evidence.append({"source":"domain","kind":"DOMAIN_FACTS","source_url":website_page.url,"hostname":hostname,"root_domain":root_domain,"subdomain":subdomain,"title":website_page.title,"meta_description":website_page.description,"core_word":core_word,"derived_words":derived_words})
            for clue in list(dict.fromkeys(x for x in (root_domain,core_word,*derived_words) if x)):
                domain_hits.extend(self._search(f'"{clue}" (HyperEVM OR Hyperliquid) (docs OR token OR contracts)',started,evidence))
                domain_hits.extend(self._search(f'site:x.com "{clue}"',started,evidence))
        unique=unique_hits(unique+domain_hits)
        for hit in domain_hits:
            match=X_RE.search(hit.url)
            if match: x_urls.append(match.group(0))

        site_x=[]
        if website_page:
            site_x=[m.group(0) for link in website_page.links for m in [X_RE.search(link)] if m]
            x_urls=site_x+x_urls; evidence.append({"source":"website","url":website_page.url,"address_match":bool(getattr(website_page,"address_match",False)),"linked_x":site_x})
        x_url=""; x_page=Page("",status="NO_DATA")
        for candidate in [u for u in dict.fromkeys(x_urls) if not _is_shared_provider_x(u)][:6]:
            if time.monotonic()-started>=self.total_timeout: break
            try: candidate_page=self.x.fetch(candidate,self._request_timeout(started))
            except Exception as exc:
                evidence.append({"source":"x_public","url":candidate,"status":"NO_DATA","detail":type(exc).__name__}); continue
            evidence.append({"source":"x_public","url":candidate,"status":candidate_page.status})
            if candidate_page.status!="AVAILABLE": continue
            if not x_url: x_url=candidate; x_page=candidate_page
            if website_page and candidate in site_x and any(_same_site(link,website_page.url) for link in candidate_page.links):
                x_url=candidate; x_page=candidate_page; break

        if not website_page and x_page.status=="AVAILABLE":
            backlink=next((link for link in x_page.links if _host(link) not in {"x.com","twitter.com","t.co"} and not _is_shared_identity_host(link)),"")
            if backlink:
                try:
                    page=self.pages.fetch(backlink,self._request_timeout(started))
                    if page.status=="AVAILABLE" and not _is_shared_identity_host(page.url):
                        blob=(page.raw+" "+page.text).lower(); page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(blob)}); website_page=page; website_candidate=page.url
                except Exception as exc: evidence.append({"source":"x_website","status":"NO_DATA","detail":type(exc).__name__})
        if website_page and not hostname:
            hostname,root_domain,subdomain=domain_parts(website_page.url); core_word=extract_core_word(website_page.url,website_page.title); derived_words=derive_core_words(core_word)

        website=website_page.url if website_page else website_candidate
        # The website may have been discovered from the X backlink above.
        # Recompute using that page before applying the unchanged cross-link rule.
        if website_page:
            site_x=[m.group(0) for link in website_page.links for m in [X_RE.search(link)] if m]
        x_links_site=bool(website and x_page.status=="AVAILABLE" and any(_same_site(link,website) for link in x_page.links))
        site_links_x=bool(website_page and site_x and x_url in site_x)
        homepage_address=bool(website_page and getattr(website_page,"address_match",False))
        all_links=[hit.url for hit in unique]+[p.url for p in direct_docs_pages]+(website_page.links if website_page else [])+(x_page.links if x_page.status=="AVAILABLE" else [])
        docs=list(dict.fromkeys(link for link in all_links if useful_docs(link)))
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
            try: page=self.pages.fetch(link,self._request_timeout(started))
            except Exception as exc: evidence.append({"source":"official_docs","url":link,"status":"NO_DATA","detail":type(exc).__name__}); continue
            if page.status=="AVAILABLE":
                blob=(page.raw+" "+page.text).lower(); page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(blob)}); trusted_docs.append(page); docs_crosslinked=True; evidence.append({"source":"official_docs","url":page.url,"address_match":page.address_match,"crosslinked":True})
        docs_address=any(getattr(page,"address_match",False) for page in trusted_docs)
        site_identity=_identity_words(root_domain,website_page.title if website_page else "",website_page.description if website_page else "")
        x_identity=_identity_words(x_page.title,x_page.description,x_page.text[:800])
        brand_consistent=bool(site_identity & x_identity)
        corpus_text=" ".join([website_page.text if website_page else "",x_page.text,*[p.text for p in trusted_docs]]).lower()
        product_context=any(word in f" {corpus_text} " for words in TYPE_RULES.values() for word in words)
        chain_context=any(word in corpus_text for word in ("hyperevm","hyper evm","hyperliquid","chain 999"))
        if homepage_address or (docs_address and docs_crosslinked): verification="VERIFIED"; verification_method="VERIFIED_BY_ADDRESS"
        elif site_links_x and x_links_site and brand_consistent and (chain_context or product_context): verification="VERIFIED"; verification_method="VERIFIED_BY_CROSS_LINK"
        elif website or x_url: verification="PARTIAL"; verification_method="PARTIAL"
        else: verification="NO_DATA"; verification_method="NO_DATA"
        official_x=x_url if verification=="PARTIAL" or site_links_x or x_links_site else ""
        evidence.append({"kind":"CROSS_VERIFICATION","website_to_x":site_links_x,"x_to_website":x_links_site,"brand_consistent":brand_consistent,"chain_context":chain_context,"product_context":product_context,"family_address_on_website":homepage_address,"family_address_in_docs":docs_address,"docs_crosslinked":docs_crosslinked,"status":verification_method})

        corpus=" ".join([family.get("platform_types") or "",website_page.text if website_page else "",website_page.description if website_page else "",x_page.text,x_page.description,*[p.text for p in trusted_docs]]).lower(); padded=f" {corpus} "; types=[]
        roles=[x.strip().upper() for x in str(family.get("platform_types") or "").split(",") if x.strip()]
        types.extend(ROLE_TYPES[x] for x in roles if x in ROLE_TYPES)
        for label,words in TYPE_RULES.items():
            if any(word in padded for word in words): types.append(label)
        types=list(dict.fromkeys(types)) or ["Other"]
        title=(website_page.title if website_page else "") or x_page.title; project_name=re.sub(r"\s*[-|].*$","",title).strip()[:120]
        description=((website_page.description if website_page else "") or x_page.description or (website_page.text[:400] if website_page else "")).strip()
        if docs: evidence.append({"source":"cross_links","kind":"DOCS","urls":docs})
        if github: evidence.append({"source":"cross_links","kind":"GITHUB","urls":github})

        token_pages=[]; token_hits=[]; token_clues=list(dict.fromkeys(x for x in (core_word,*derived_words) if x)); token_targets=[f"site:{root_domain}"] if root_domain else []
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
                try: page=self.pages.fetch(url,self._request_timeout(started))
                except Exception as exc:
                    evidence.append({"source":"official_token_page","url":url,"status":"NO_DATA","detail":type(exc).__name__}); continue
                if page.status!="AVAILABLE": continue
                token_pages.append((page,"official_docs" if _is_docs_url(page.url) else "website"))
                evidence.append({"source":"official_token_page","url":page.url,"status":"AVAILABLE"})
                for link in page.links:
                    if _looks_like_token_detail_url(link,root_domain) and link.split("#",1)[0] not in seen_token_pages:
                        token_queue.append(link)
        chain_token_candidates=[]
        # Search DEX Screener by the full project core word and 3/4/5 derivatives.
        # Results are filtered back to HyperEVM and scored only as discovery candidates.
        # This lets a platform token be noticed before its CA is indexed by web search.
        for clue in token_clues:
            if time.monotonic()-started>=self.total_timeout: break
            try:
                response=requests.get(
                    "https://api.dexscreener.com/latest/dex/search",
                    params={"q": clue}, timeout=self._request_timeout(started),
                    headers={"Accept":"application/json","User-Agent":"HyperEVM-Radar/3.2"},
                )
                response.raise_for_status(); rows=(response.json() or {}).get("pairs") or []
                found=0
                for row in rows[:30]:
                    if str(row.get("chainId") or "").lower() not in {"hyperevm","hyperliquid"}: continue
                    for side in ("baseToken","quoteToken"):
                        token=row.get(side) or {}; ca=str(token.get("address") or "").strip().lower()
                        name=str(token.get("name") or "").strip(); symbol=str(token.get("symbol") or "").strip()
                        if not ADDRESS_RE.fullmatch(ca): continue
                        words=_identity_words(name,symbol)
                        normalized_symbol=re.sub(r"[^a-z0-9]","",symbol.lower())
                        if clue.lower() not in words and normalized_symbol!=clue.lower() and clue.lower() not in re.sub(r"[^a-z0-9]","",name.lower()): continue
                        item={"kind":"chain_dex_candidate","url":str(row.get("url") or ""),"token_name":name,"token_symbol":symbol,"token_ca":ca,"trusted":False,"clue":clue,"pair_address":str(row.get("pairAddress") or "")}
                        key=(ca,symbol.lower())
                        if key not in {(x["token_ca"],x["token_symbol"].lower()) for x in chain_token_candidates}:
                            chain_token_candidates.append(item); found+=1
                evidence.append({"source":"dexscreener_token_search","query":clue,"status":"HITS" if found else "NO_DATA","hits":found})
            except Exception as exc:
                evidence.append({"source":"dexscreener_token_search","query":clue,"status":"NO_DATA","detail":type(exc).__name__})
        if x_url: token_targets.append(f"site:x.com/{urlparse(x_url).path.strip('/').split('/')[0]}")
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
        for hit in unique_hits(token_hits)[:self.max_hits]:
            if time.monotonic()-started>=self.total_timeout: break
            is_x=_host(hit.url) in {"x.com","twitter.com"}; same_official_x=bool(is_x and x_url and urlparse(hit.url).path.strip("/").split("/")[0].lower()==urlparse(x_url).path.strip("/").split("/")[0].lower())
            if not (root_domain and domain_parts(hit.url)[1]==root_domain) and not same_official_x: continue
            try: page=(self.x if is_x else self.pages).fetch(hit.url,self._request_timeout(started))
            except Exception: continue
            if page.status=="AVAILABLE": token_pages.append((page,"official_x" if is_x else "official_docs" if "docs" in _host(page.url) or "/docs" in page.url else "website"))

        token_sources=list(chain_token_candidates)
        # Extract CA candidates from context on official pages. This supplements the
        # existing structured JSON metadata parser; it does not replace it.
        if website_page:
            token_sources.extend(_contextual_token_sources(website_page,token_clues,"website",True,addresses))
        for page in trusted_docs:
            token_sources.extend(_contextual_token_sources(page,token_clues,"official_docs",True,addresses))
        if x_page.status=="AVAILABLE" and (site_links_x or x_links_site):
            token_sources.extend(_contextual_token_sources(x_page,token_clues,"official_x",True,addresses))
        for page,kind in token_pages:
            trusted=kind!="official_x" or site_links_x or x_links_site
            if kind in {"website","official_docs","official_x"}:
                token_sources.extend(_contextual_token_sources(page,token_clues,kind,trusted,addresses))
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
        if website_page: token_sources.append({"kind":"website","url":website_page.url,"token_name":website_page.token_name,"token_symbol":website_page.token_symbol,"token_ca":website_page.token_ca,"trusted":True})
        if x_page.status=="AVAILABLE": token_sources.append({"kind":"official_x","url":x_page.url,"token_name":x_page.token_name,"token_symbol":x_page.token_symbol,"token_ca":x_page.token_ca,"trusted":site_links_x or x_links_site})
        token_sources.extend({"kind":"official_docs","url":p.url,"token_name":p.token_name,"token_symbol":p.token_symbol,"token_ca":p.token_ca,"trusted":True} for p in trusted_docs)
        token_sources.extend({"kind":kind,"url":p.url,"token_name":p.token_name,"token_symbol":p.token_symbol,"token_ca":p.token_ca,"trusted":kind!="official_x" or site_links_x or x_links_site} for p,kind in token_pages)
        dex_names=[x.get("token_name") for x in chain_token_candidates if x.get("token_name")]
        dex_symbols=[x.get("token_symbol") for x in chain_token_candidates if x.get("token_symbol")]
        token_result=discover_official_token(website,core_word,derived_words,token_sources,[*(family.get("token_names") or []),*dex_names],[*(family.get("token_symbols") or []),*dex_symbols])
        token_ca=token_result["official_token_ca"]; token_symbol=token_result["official_token_symbol"]; token_status="CONFIRMED_OFFICIAL" if token_result["token_verification_status"]=="VERIFIED" else "NONE"
        token_evidence_source=token_result["token_evidence"][0]["source"] if token_result["token_evidence"] else ""
        evidence.extend({**item,"kind":"OFFICIAL_TOKEN_CA" if token_status=="CONFIRMED_OFFICIAL" else "TOKEN_CANDIDATE"} for item in token_result["token_evidence"])
        evidence.append({"kind":"INFRASTRUCTURE_CLASSIFICATION","types":types,"role_inputs":roles})
        discovered_project_addresses=[]
        for page,source in [(website_page,"website"),(x_page,"x_public")]+[(page,"official_docs") for page in trusted_docs]:
            for item in _project_addresses(page,source):
                if (item["address"],item["source_url"]) not in {(x["address"],x["source_url"]) for x in discovered_project_addresses}: discovered_project_addresses.append(item)
        execution_status='BUDGET_EXHAUSTED' if time.monotonic()-started>=self.total_timeout else 'ERROR' if any(e.get('detail')=='SearchUnavailable' for e in evidence) else 'COMPLETE'
        evidence.append({'source':'identity_execution','status':'ERROR' if execution_status!='COMPLETE' else 'COMPLETE','detail':execution_status,'elapsed_seconds':round(time.monotonic()-started,2)})
        return {
            'execution_status':execution_status,
            "family_id":family.get("id"),"subject_key":family.get("subject_key") or (f"family:{family.get('id')}" if family.get("id") else ""),"project_name":project_name,"official_x":official_x,"official_website":website,"description":description[:700],"infrastructure_types":types,
            "official_token_symbol":token_symbol,"official_token_ca":token_ca,"token_status":token_status,"source_url":website,"hostname":hostname,"root_domain":root_domain,"subdomain":subdomain,"core_word":core_word,"derived_words":derived_words,
            "discovered_token_name":token_result["discovered_token_name"],"discovered_token_symbol":token_result["discovered_token_symbol"],"discovered_ca":token_result["discovered_ca"],"evidence_source":token_evidence_source,"token_verification_status":token_result["token_verification_status"],"token_evidence":token_result["token_evidence"],"token_source_urls":token_result["token_source_urls"],
            "verification_method":verification_method,"verification_status":verification,"confidence":90 if verification=="VERIFIED" else 55 if verification=="PARTIAL" else 0,"docs":docs,"github":github,"discovered_project_addresses":discovered_project_addresses,"discovered_candidates":len(unique),"evidence":evidence,
        }
