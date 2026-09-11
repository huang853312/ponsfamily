from web3 import Web3

PAIR_CREATED_TOPIC = Web3.keccak(
    text="PairCreated(address,address,address,uint256)"
).hex()

POOL_CREATED_TOPIC = Web3.keccak(
    text="PoolCreated(address,address,uint24,int24,address)"
).hex()


def _topic_address(topic):
    raw = bytes(topic)
    return "0x" + raw[-20:].hex()


def detect_pool_event(log):
    topics = log.get("topics", [])
    if not topics:
        return None

    topic0 = topics[0].hex()

    # Uniswap V2-style PairCreated
    if topic0 == PAIR_CREATED_TOPIC and len(topics) >= 3:
        data = bytes(log.get("data", b""))
        if len(data) < 32:
            return None

        token0 = _topic_address(topics[1])
        token1 = _topic_address(topics[2])
        pool = "0x" + data[12:32].hex()

        return {
            "type": "V2_PAIR",
            "factory": log["address"],
            "token0": token0,
            "token1": token1,
            "pool": pool,
        }

    # Uniswap V3-style PoolCreated
    if topic0 == POOL_CREATED_TOPIC and len(topics) >= 4:
        data = bytes(log.get("data", b""))
        if len(data) < 64:
            return None

        token0 = _topic_address(topics[1])
        token1 = _topic_address(topics[2])
        fee = int.from_bytes(bytes(topics[3])[-3:], "big")
        pool = "0x" + data[44:64].hex()

        return {
            "type": "V3_POOL",
            "factory": log["address"],
            "token0": token0,
            "token1": token1,
            "pool": pool,
            "fee": fee,
        }

    return None
