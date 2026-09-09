from eth_abi import decode
from web3 import Web3

TOPIC = Web3.keccak(text="PoolCreated(address,address,uint24,int24,address)").hex()

def decode_log(log):
    token0="0x"+log["topics"][1].hex()[-40:]; token1="0x"+log["topics"][2].hex()[-40:]
    # fee is indexed in topics[3]; only tickSpacing and pool are in event data.
    _,pool=decode(["int24","address"],bytes(log["data"]))
    return token0.lower(), token1.lower(), pool.lower(), []
