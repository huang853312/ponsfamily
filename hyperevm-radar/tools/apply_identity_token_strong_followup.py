from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "platform_family_intelligence.py"
text = TARGET.read_text()

old = '''def _contextual_token_sources(page, clues, kind, trusted=False):
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
new = '''def _contextual_token_sources(page, clues, kind, trusted=False, excluded_addresses=()):
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
'''
assert old in text, "contextual token helper anchor not found"
text=text.replace(old,new,1)

text=text.replace('_contextual_token_sources(website_page,token_clues,"website",True)', '_contextual_token_sources(website_page,token_clues,"website",True,addresses)')
text=text.replace('_contextual_token_sources(page,token_clues,"official_docs",True)', '_contextual_token_sources(page,token_clues,"official_docs",True,addresses)')
text=text.replace('_contextual_token_sources(x_page,token_clues,"official_x",True)', '_contextual_token_sources(x_page,token_clues,"official_x",True,addresses)')
text=text.replace('_contextual_token_sources(page,token_clues,kind,trusted)', '_contextual_token_sources(page,token_clues,kind,trusted,addresses)')

TARGET.write_text(text)
print("Applied family-address exclusion to contextual token discovery")
