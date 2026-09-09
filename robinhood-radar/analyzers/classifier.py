"""Conservative, explainable contract classification from verifiable evidence."""
from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class Classification:
    label: str
    confidence: float
    evidence: tuple[str,...]
    def as_dict(self):
        value=asdict(self); value["evidence"]=list(self.evidence); return value

# Multiple selectors are required for every semantic label. Selectors are canonical ABI signatures.
RULES={
 "ERC20": ({"0x18160ddd","0x70a08231","0xa9059cbb","0x23b872dd"},.98,"ERC20 core selector set"),
 "Vault": ({"0x38d52e0f","0x6e553f65","0xba087652","0xb460af94"},.90,"ERC-4626 asset/deposit/mint/withdraw selectors"),
 "Router": ({"0x38ed1739","0x18cbafe5","0x7ff36ab5"},.88,"multiple Uniswap-style swap selectors"),
 "Factory": ({"0xc9c65396","0xe6a43905","0x574f2ba3"},.84,"pair creation and pair lookup selector family"),
 "Lending": ({"0xa415bcad","0x573ade81","0xdb006a75"},.82,"borrow/repay/liquidation selector family"),
 "Oracle": ({"0xfeaf968c","0x313ce567","0x50d25bcd"},.80,"round-data/decimals/latest-answer oracle family"),
 "Aggregator": ({"0x12aa3caf","0x0502b1c5"},.78,"aggregate swap/quote selector family"),
 "Bridge": ({"0x9a2ac6d5","0x21d0f7a7"},.76,"deposit/withdraw bridge selector family"),
 "Registry": ({"0x02571be3","0x8da5cb5b","0xf2fde38b"},.76,"registry lookup and ownership selector family"),
 "Settlement": ({"0x13d79a0b","0x3d4dff7b"},.76,"settlement/invalidate-order selector family"),
 "AMM": ({"0x022c0d9f","0x0902f1ac","0x6a627842"},.88,"swap/reserves/mint AMM selector family"),
}

def classify(selectors, *, is_minimal_proxy=False, implementation=None, role_hint=None):
    found=set(selectors); evidence=[]
    if is_minimal_proxy:
        return Classification("Proxy",.99,("EIP-1167 minimal proxy runtime pattern",f"implementation={implementation}"))
    if implementation:
        return Classification("Proxy",.99,("non-zero EIP-1967 implementation slot",f"implementation={implementation}"))
    candidates=[]
    for label,(required,confidence,description) in RULES.items():
        overlap=required & found
        minimum=3 if len(required)>=3 else 2
        if len(overlap)>=minimum: candidates.append((confidence,label,description,overlap))
    if len(candidates)==1:
        confidence,label,description,overlap=candidates[0]
        return Classification(label,confidence,(description,"selectors="+",".join(sorted(overlap))))
    if role_hint in {"Hook","Pool","Implementation"}:
        # Role comes from an on-chain relationship, not a name or lone selector.
        return Classification(role_hint,.95,(f"on-chain relationship identifies {role_hint}",))
    if candidates:
        evidence=[f"ambiguous matches: {','.join(x[1] for x in candidates)}"]
    return Classification("Unknown",0.0,tuple(evidence or ["insufficient selector/relationship evidence"]))
