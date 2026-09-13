#!/usr/bin/env python3
"""Apply the narrow GitBook identity guard without changing the canonical radar flow."""
from pathlib import Path
import sys

path = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "platform_family_intelligence.py")
text = path.read_text()
original = text


def replace_once(old: str, new: str) -> None:
    global text
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"Expected source fragment not found:\n{old[:220]}")
    text = text.replace(old, new, 1)


replace_once(
    'def _host(url): return (urlparse(url).hostname or "").lower().removeprefix("www.")\ndef _same_site(a,b): return bool(_host(a) and _host(a)==_host(b))\n',
    'def _host(url): return (urlparse(url).hostname or "").lower().removeprefix("www.")\ndef _same_site(a,b): return bool(_host(a) and _host(a)==_host(b))\n\n# Shared documentation infrastructure is useful discovery evidence, but it is not\n# the investigated project itself. Keep this deliberately narrow to the confirmed\n# GitBook false-positive class.\nSHARED_IDENTITY_HOSTS={"gitbook.com","gitbook.io"}\nSHARED_IDENTITY_X_HANDLES={"gitbookio"}\n\ndef _is_shared_identity_host(url):\n    host=_host(url)\n    return bool(host and (host in SHARED_IDENTITY_HOSTS or host.endswith(".gitbook.io")))\n\ndef _is_shared_provider_x(url):\n    match=X_RE.search(str(url or ""))\n    return bool(match and match.group(1).lower() in SHARED_IDENTITY_X_HANDLES)\n'
)

replace_once(
    'def extract_core_word(url, title=""):\n    _,root,_=domain_parts(url); label=root.split(".")[0] if root else ""\n    return "".join(x.lower() for x in re.findall(r"[A-Za-z0-9]+",label) if x)\n',
    'def extract_core_word(url, title=""):\n    if _is_shared_identity_host(url): return ""\n    _,root,_=domain_parts(url); label=root.split(".")[0] if root else ""\n    return "".join(x.lower() for x in re.findall(r"[A-Za-z0-9]+",label) if x)\n'
)

replace_once(
    '            if match:\n                x_urls.append(match.group(0)); evidence.append({"source":hit.source,"kind":"X_CANDIDATE","url":match.group(0),"title":hit.title[:160]})\n            elif _is_docs_url(hit.url):\n',
    '            if match:\n                candidate_x=match.group(0)\n                if _is_shared_provider_x(candidate_x):\n                    evidence.append({"source":hit.source,"kind":"REJECTED_SHARED_PROVIDER_X","url":candidate_x,"title":hit.title[:160]})\n                else:\n                    x_urls.append(candidate_x); evidence.append({"source":hit.source,"kind":"X_CANDIDATE","url":candidate_x,"title":hit.title[:160]})\n            elif _is_docs_url(hit.url):\n'
)

replace_once(
    '            elif _host(hit.url)=="github.com":\n                github_hits.append(hit); evidence.append({"source":hit.source,"kind":"GITHUB_CANDIDATE","url":hit.url,"title":hit.title[:160]})\n            elif _host(hit.url) not in excluded:\n                web_hits.append(hit)\n',
    '            elif _host(hit.url)=="github.com":\n                github_hits.append(hit); evidence.append({"source":hit.source,"kind":"GITHUB_CANDIDATE","url":hit.url,"title":hit.title[:160]})\n            elif _is_shared_identity_host(hit.url):\n                evidence.append({"source":hit.source,"kind":"REJECTED_SHARED_IDENTITY_HOST","url":hit.url,"title":hit.title[:160]})\n            elif _host(hit.url) not in excluded:\n                web_hits.append(hit)\n'
)

replace_once(
    '                if match:\n                    x_urls.append(match.group(0)); evidence.append({"source":"github_discovery","kind":"X_CANDIDATE","url":match.group(0)})\n                elif _is_docs_url(link):\n',
    '                if match:\n                    candidate_x=match.group(0)\n                    if _is_shared_provider_x(candidate_x):\n                        evidence.append({"source":"github_discovery","kind":"REJECTED_SHARED_PROVIDER_X","url":candidate_x})\n                    else:\n                        x_urls.append(candidate_x); evidence.append({"source":"github_discovery","kind":"X_CANDIDATE","url":candidate_x})\n                elif _is_docs_url(link):\n'
)

replace_once(
    '                if match:\n                    x_urls.append(match.group(0))\n                    evidence.append({"source":"direct_docs","kind":"X_CANDIDATE","url":match.group(0)})\n                    continue\n',
    '                if match:\n                    candidate_x=match.group(0)\n                    if _is_shared_provider_x(candidate_x):\n                        evidence.append({"source":"direct_docs","kind":"REJECTED_SHARED_PROVIDER_X","url":candidate_x})\n                    else:\n                        x_urls.append(candidate_x)\n                        evidence.append({"source":"direct_docs","kind":"X_CANDIDATE","url":candidate_x})\n                    continue\n'
)

replace_once(
    '            if page.status!="AVAILABLE": continue\n            if _is_parked_page(page):\n',
    '            if page.status!="AVAILABLE": continue\n            if _is_shared_identity_host(page.url):\n                evidence.append({"source":"website","url":page.url,"status":"REJECTED_SHARED_IDENTITY_HOST"}); continue\n            if _is_parked_page(page):\n'
)

replace_once(
    '        x_url=next(iter(dict.fromkeys(x_urls)),""); x_page=Page(x_url,status="NO_DATA")\n',
    '        x_url=next((url for url in dict.fromkeys(x_urls) if not _is_shared_provider_x(url)),""); x_page=Page(x_url,status="NO_DATA")\n'
)

replace_once(
    '            backlink=next((link for link in x_page.links if _host(link) not in {"x.com","twitter.com","t.co"}),"")\n',
    '            backlink=next((link for link in x_page.links if _host(link) not in {"x.com","twitter.com","t.co"} and not _is_shared_identity_host(link)),"")\n'
)

replace_once(
    '                    if page.status=="AVAILABLE":\n                        blob=(page.raw+" "+page.text).lower(); page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(blob)}); website_page=page; website_candidate=page.url\n',
    '                    if page.status=="AVAILABLE" and not _is_shared_identity_host(page.url):\n                        blob=(page.raw+" "+page.text).lower(); page.address_match=bool(addresses & {x.lower() for x in ADDRESS_RE.findall(blob)}); website_page=page; website_candidate=page.url\n'
)

if text != original:
    path.write_text(text)
    print(f"patched {path}")
else:
    print(f"already patched {path}")
