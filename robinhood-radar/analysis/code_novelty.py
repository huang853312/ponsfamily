"""Deterministic runtime-code fingerprints. Novelty is a signal, never an A/S vote."""
from dataclasses import dataclass
from web3 import Web3

@dataclass(frozen=True)
class CodeFingerprint:
    runtime_hash: str
    normalized_hash: str
    selectors: tuple[str,...]
    event_topics: tuple[str,...]
    implementation: str|None
    eip1167_implementation: str|None
    code_size: int
    template_status: str
    similarity: float
    evidence: tuple[str,...]

def normalize_runtime(code:bytes)->bytes:
    """Remove valid Solidity CBOR metadata suffix while preserving executable code."""
    if len(code)<2:return code
    length=int.from_bytes(code[-2:],"big")
    return code[:-(length+2)] if 0<length+2<=len(code) else code

def fingerprint(code,selectors=(),event_topics=(),implementation=None,is_eip1167=False,known=()):
    normalized=normalize_runtime(code);runtime_hash=Web3.keccak(code).hex();normalized_hash=Web3.keccak(normalized).hex()
    exact=next((x for x in known if x["normalized_hash"]==normalized_hash),None)
    similarities=[]
    current=set(selectors)
    for item in known:
        other=set(item.get("selectors",()))
        similarities.append(len(current&other)/len(current|other) if current|other else 1.0)
    similarity=max(similarities,default=0.0)
    if exact:status="KNOWN_TEMPLATE";evidence=("normalized runtime hash exact match",)
    elif similarity>=.6:status="SIMILAR_TEMPLATE";evidence=(f"selector Jaccard similarity={similarity:.2f}",)
    else:status="UNKNOWN_STRUCTURE";evidence=("no exact or >=0.60 selector-set match",)
    return CodeFingerprint(runtime_hash,normalized_hash,tuple(sorted(selectors)),tuple(sorted(event_topics)),implementation,
        implementation if is_eip1167 else None,len(code),status,similarity,evidence)

