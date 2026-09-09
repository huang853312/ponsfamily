"""Cached bytecode, selector, and proxy inspection."""
import re
from web3 import Web3

EIP1967_IMPLEMENTATION_SLOT=int("360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc",16)
MINIMAL_PROXY=re.compile(rb"^\x36\x3d\x3d\x37\x3d\x3d\x3d\x36\x3d\x73(.{20})\x5a\xf4\x3d\x82\x80\x3e\x90\x3d\x91\x60\x2b\x57\xfd\x5b\xf3$")

def extract_selectors(code: bytes) -> tuple[str,...]:
    return tuple(sorted({"0x"+code[i+1:i+5].hex() for i,b in enumerate(code[:-4]) if b==0x63}))

def minimal_proxy_implementation(code: bytes) -> str | None:
    match=MINIMAL_PROXY.match(code)
    return Web3.to_checksum_address(match.group(1).hex()).lower() if match else None

class ContractInspector:
    def __init__(self,limiter=None): self._cache,self.limiter={},limiter
    async def _call(self,awaitable):return await self.limiter.call(awaitable) if self.limiter else await awaitable
    async def inspect(self,w3,address):
        address=address.lower()
        if address in self._cache: return self._cache[address]
        code=bytes(await self._call(w3.eth.get_code(Web3.to_checksum_address(address))))
        minimal=minimal_proxy_implementation(code)
        raw=bytes(await self._call(w3.eth.get_storage_at(Web3.to_checksum_address(address),EIP1967_IMPLEMENTATION_SLOT)))
        slot_impl=None if not any(raw) else "0x"+raw[-20:].hex()
        result={"code_hash":Web3.keccak(code).hex(),"selectors":extract_selectors(code),"size":len(code),
                "is_minimal_proxy":minimal is not None,"implementation":minimal or slot_impl,"runtime_code":code}
        self._cache[address]=result
        return result

# Compatibility helper for integrations.
async def inspect_contract(w3,address): return await ContractInspector().inspect(w3,address)
