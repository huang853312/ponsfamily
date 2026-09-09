"""Conservative bytecode selector classifier; uncertain evidence remains Unknown."""
SELECTORS={
 "ERC20":{"0x70a08231","0xa9059cbb","0x18160ddd"},
 "Proxy":{"0x5c60da1b"},
 "Vault":{"0x6e553f65","0xba087652"},
 "Router":{"0x38ed1739","0x7ff36ab5"},
}

def classify(selectors):
    found=set(selectors)
    matches=[name for name, required in SELECTORS.items() if required <= found]
    return matches[0] if len(matches)==1 else "Unknown"

