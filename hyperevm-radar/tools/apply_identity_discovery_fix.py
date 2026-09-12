from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "platform_family_intelligence.py"
text = PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


# Strengthen platform-token discovery while preserving the hard rule:
# chain/DEX evidence may create TOKEN_CANDIDATE only; only website/Docs/official X
# can produce CONFIRMED_OFFICIAL.
anchor = '''        token_pages=[]; token_hits=[]; token_clues=list(dict.fromkeys(x for x in (core_word,*derived_words) if x)); token_targets=[f"site:{root_domain}"] if root_domain else []
'''
addition = '''        token_pages=[]; token_hits=[]; token_clues=list(dict.fromkeys(x for x in (core_word,*derived_words) if x)); token_targets=[f"site:{root_domain}"] if root_domain else []
        chain_token_candidates=[]
        # Search DEX Screener by the full project core word and 3/4/5 derivatives.
        # Results are filtered back to HyperEVM and scored only as discovery candidates.
        # This lets a platform token be noticed before its CA is indexed by web search.
        for clue in token_clues:
            if time.monotonic()-started>=self.total_timeout: break
            try:
                response=requests.get(
                    "https://api.dexscreener.com/latest/dex/search",
                    params={"q": clue}, timeout=self.request_timeout,
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
'''
replace_once(anchor, addition, "dex platform token candidates")

old = '''        token_sources=[]
        if website_page: token_sources.append({"kind":"website","url":website_page.url,"token_name":website_page.token_name,"token_symbol":website_page.token_symbol,"token_ca":website_page.token_ca,"trusted":True})
'''
new = '''        token_sources=list(chain_token_candidates)
        if website_page: token_sources.append({"kind":"website","url":website_page.url,"token_name":website_page.token_name,"token_symbol":website_page.token_symbol,"token_ca":website_page.token_ca,"trusted":True})
'''
replace_once(old, new, "feed chain candidates into token discovery")

# Preserve candidate name/symbol from DEX metadata even when the family did not
# already know it. Official fields remain empty until a trusted source confirms CA.
old = '''        token_result=discover_official_token(website,core_word,derived_words,token_sources,family.get("token_names") or [],family.get("token_symbols") or [])
'''
new = '''        dex_names=[x.get("token_name") for x in chain_token_candidates if x.get("token_name")]
        dex_symbols=[x.get("token_symbol") for x in chain_token_candidates if x.get("token_symbol")]
        token_result=discover_official_token(website,core_word,derived_words,token_sources,[*(family.get("token_names") or []),*dex_names],[*(family.get("token_symbols") or []),*dex_symbols])
'''
replace_once(old, new, "include DEX token names and symbols")

PATH.write_text(text, encoding="utf-8")
print("patched", PATH)
