import hashlib
import re

def fingerprint(code: bytes):
    code_hash = hashlib.sha256(code).hexdigest()
    code_size = len(code)

    selectors = set()
    i = 0
    while i < len(code) - 4:
        if code[i] == 0x63:
            selectors.add("0x" + code[i+1:i+5].hex())
            i += 5
        else:
            i += 1

    is_eip1167 = (
        b"\x36\x3d\x3d\x37\x3d\x3d\x3d\x36\x3d\x73" in code
        and b"\x5a\xf4\x3d\x82\x80\x3e\x90\x3d\x91\x60\x2b\x57\xfd\x5b\xf3" in code
    )

    implementation = ""
    match = re.search(
        rb"\x36\x3d\x3d\x37\x3d\x3d\x3d\x36\x3d\x73(.{20})",
        code,
        re.DOTALL,
    )
    if match:
        implementation = "0x" + match.group(1).hex()

    eip1967_hint = bytes.fromhex(
        "360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
    ) in code

    template_key = hashlib.sha256(
        ",".join(sorted(selectors)).encode()
    ).hexdigest()

    return {
        "code_hash": code_hash,
        "code_size": code_size,
        "selectors": sorted(selectors),
        "is_eip1167": is_eip1167,
        "implementation": implementation,
        "eip1967_hint": eip1967_hint,
        "template_key": template_key,
    }
