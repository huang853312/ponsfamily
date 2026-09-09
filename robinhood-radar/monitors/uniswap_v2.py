from eth_abi import decode
from web3 import Web3

TOPIC = Web3.keccak(text="PairCreated(address,address,address,uint256)").hex()

def decode_log(log):
    token0="0x"+log["topics"][1].hex()[-40:]; token1="0x"+log["topics"][2].hex()[-40:]
    pair,_=decode(["address","uint256"],bytes(log["data"]))
    return token0.lower(), token1.lower(), pair.lower(), []
