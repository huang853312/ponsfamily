from web3 import Web3

async def inspect_contract(w3, address):
    code=bytes(await w3.eth.get_code(address))
    selectors=sorted({"0x"+code[i+1:i+5].hex() for i,b in enumerate(code[:-4]) if b==0x63})
    return {"code_hash":Web3.keccak(code).hex(),"selectors":selectors,"size":len(code)}

