from eth_abi import decode
from web3 import Web3

TOPIC = Web3.keccak(text="Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)").hex()

def decode_log(log):
    # V4 pools live inside PoolManager: use pool id as the stable synthetic address/key.
    pool_id="0x"+log["topics"][1].hex().removeprefix("0x"); token0="0x"+log["topics"][2].hex()[-40:]; token1="0x"+log["topics"][3].hex()[-40:]
    _,_,hook,_,_=decode(["uint24","int24","address","uint160","int24"],bytes(log["data"]))
    related=[] if int(hook,16)==0 else [(hook.lower(),"Hook")]
    return token0.lower(), token1.lower(), pool_id.lower(), related
