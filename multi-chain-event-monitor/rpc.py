import itertools
import requests


class JsonRpcError(RuntimeError):
    pass


class RpcClient:
    _ids = itertools.count(1)

    def __init__(self, url: str, timeout: int = 15):
        self.url = url
        self.timeout = timeout
        self.session = requests.Session()

    def call(self, method: str, params: list):
        request_id = next(self._ids)
        response = self.session.post(
            self.url,
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("error"):
            raise JsonRpcError(f"{method}: {body['error']}")
        if "result" not in body:
            raise JsonRpcError(f"{method}: missing result")
        return body["result"]

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def get_logs(self, start: int, end: int, event_filter: dict):
        query = {"fromBlock": hex(start), "toBlock": hex(end)}
        if event_filter.get("addresses"):
            addresses = event_filter["addresses"]
            query["address"] = addresses[0] if len(addresses) == 1 else addresses
        if event_filter.get("topics"):
            query["topics"] = event_filter["topics"]
        return self.call("eth_getLogs", [query])
