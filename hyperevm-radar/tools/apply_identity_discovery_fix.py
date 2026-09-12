from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "platform_family_intelligence.py"
text = PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


# Strengthen the two weak links without changing verification semantics:
# address -> website and address -> official X.
# DEX Screener is used only as candidate-discovery metadata; explorer contract
# names are only extra search clues. Neither source can make a project VERIFIED.

anchor = '''    def _search(self, query, started, evidence):
        if time.monotonic()-started>=self.total_timeout: return []
        try:
            rows=self.search.search(query,self.request_timeout)
            evidence.append({"source":"search","query":query[:180],"status":"HITS" if rows else "NO_DATA","hits":len(rows)})
            return rows
        except Exception as exc:
            evidence.append({"source":"search","query":query[:180],"status":"NO_DATA","detail":type(exc).__name__}); return []
'''
addition = anchor + '''\n    def _direct_address_identity(self, address, started, evidence):\n        """Return non-search-engine website/X candidates and contract-name clues."""\n        hits=[]; clues=[]\n        if time.monotonic()-started>=self.total_timeout: return hits,clues\n        # DEX Screener exposes website/social metadata attached to a token/pair.\n        # Treat it as discovery evidence only; normal cross-verification still applies.\n        try:\n            response=requests.get(\n                f"https://api.dexscreener.com/token-pairs/v1/hyperevm/{address}",\n                timeout=self.request_timeout,\n                headers={"Accept":"application/json","User-Agent":"HyperEVM-Radar/3.1"},\n            )\n            response.raise_for_status(); rows=response.json()\n            if not isinstance(rows,list): rows=[]\n            for row in rows[:8]:\n                info=row.get("info") or {}\n                for item in info.get("websites") or []:\n                    url=str((item or {}).get("url") or "").strip()\n                    if url.startswith(("http://","https://")):\n                        hits.append(SearchHit(url,source="DEXSCREENER_ADDRESS_METADATA"))\n                for item in info.get("socials") or []:\n                    item=item or {}; platform=str(item.get("platform") or "").lower(); handle=str(item.get("handle") or item.get("url") or "").strip()\n                    if platform in {"twitter","x"} and handle:\n                        if handle.startswith(("http://","https://")): url=handle\n                        else: url="https://x.com/"+handle.lstrip("@/")\n                        hits.append(SearchHit(url,source="DEXSCREENER_ADDRESS_METADATA"))\n                for side in ("baseToken","quoteToken"):\n                    token=row.get(side) or {}\n                    if str(token.get("address") or "").lower()==address.lower():\n                        for value in (token.get("name"),token.get("symbol")):\n                            value=str(value or "").strip()\n                            if len(value)>=3 and value.lower() not in {"token","unknown"}: clues.append(value)\n            evidence.append({"source":"dexscreener_address_metadata","address":address,"status":"HITS" if hits else "NO_DATA","hits":len(hits)})\n        except Exception as exc:\n            evidence.append({"source":"dexscreener_address_metadata","address":address,"status":"NO_DATA","detail":type(exc).__name__})\n\n        # Explorer pages are available immediately after deployment even when web\n        # search has not indexed the address. A verified contract name becomes a\n        # search clue, never an identity assertion.\n        if time.monotonic()-started<self.total_timeout:\n            try:\n                page=self.pages.fetch(f"https://hyperevmscan.io/address/{address}",self.request_timeout)\n                blob=" ".join((page.title,page.description,page.text[:12000])) if page.status=="AVAILABLE" else ""\n                match=re.search(r"Contract Name\\s+([A-Za-z][A-Za-z0-9_.$-]{2,80})",blob,re.I)\n                name=match.group(1).strip() if match else ""\n                generic=("proxy","erc20","token","contract","transparentupgradeableproxy","beaconproxy")\n                if name and name.lower() not in generic and not name.lower().endswith("proxy"):\n                    clues.append(name)\n                evidence.append({"source":"hyperevmscan_address","address":address,"status":page.status,"contract_name":name[:100]})\n            except Exception as exc:\n                evidence.append({"source":"hyperevmscan_address","address":address,"status":"NO_DATA","detail":type(exc).__name__})\n        return hits,list(dict.fromkeys(clues))\n'''
replace_once(anchor, addition, "direct address identity helper")

old = '''        started=time.monotonic(); ordered_addresses=_ordered_addresses(family); addresses=set(ordered_addresses); evidence=[]; hits=[]
        # Search creator first, then up to two member addresses. Exact-address query
        # is primary; a second chain-context query catches results whose index omitted
        # the word "official". This fixes the old single-query blind spot.
        for address in ordered_addresses[:3]:
            hits.extend(self._search(f'"{address}"',started,evidence))
            hits.extend(self._search(f'"{address}" HyperEVM OR Hyperliquid',started,evidence))
            hits.extend(self._search(f'site:x.com "{address}"',started,evidence))
            hits.extend(self._search(f'"{address}" (docs OR documentation OR contracts)',started,evidence))
        text_clues=[]
'''
new = '''        started=time.monotonic(); ordered_addresses=_ordered_addresses(family); addresses=set(ordered_addresses); evidence=[]; hits=[]; direct_clues=[]
        # First ask sources that know the address directly. This avoids waiting for
        # a public search engine to index a brand-new HyperEVM deployment.
        for address in ordered_addresses[:3]:
            direct_hits,new_clues=self._direct_address_identity(address,started,evidence)
            hits.extend(direct_hits); direct_clues.extend(new_clues)
            hits.extend(self._search(f'"{address}"',started,evidence))
            hits.extend(self._search(f'"{address}" HyperEVM OR Hyperliquid',started,evidence))
            hits.extend(self._search(f'site:x.com "{address}"',started,evidence))
            hits.extend(self._search(f'"{address}" (docs OR documentation OR contracts)',started,evidence))
        text_clues=[]
        for x in direct_clues:
            x=str(x or "").strip()
            if len(x)>=3 and x.lower() not in {y.lower() for y in text_clues}: text_clues.append(x)
'''
replace_once(old, new, "direct address discovery integration")

# Search direct metadata/explorer names before weaker family hints, and add a
# website-oriented query so a contract name can resolve straight to a homepage.
old = '''        for clue in text_clues[:2]:
            hits.extend(self._search(f'"{clue}" HyperEVM',started,evidence))
            hits.extend(self._search(f'site:x.com "{clue}" HyperEVM',started,evidence))
            hits.extend(self._search(f'"{clue}" docs HyperEVM',started,evidence))
'''
new = '''        for clue in text_clues[:4]:
            hits.extend(self._search(f'"{clue}" HyperEVM',started,evidence))
            hits.extend(self._search(f'"{clue}" HyperEVM (official OR app OR protocol)',started,evidence))
            hits.extend(self._search(f'site:x.com "{clue}" HyperEVM',started,evidence))
            hits.extend(self._search(f'"{clue}" docs HyperEVM',started,evidence))
'''
replace_once(old, new, "expanded direct clue search")

PATH.write_text(text, encoding="utf-8")
print("patched", PATH)
