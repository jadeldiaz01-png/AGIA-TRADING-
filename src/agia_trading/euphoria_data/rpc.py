from __future__ import annotations

from dataclasses import dataclass
from json import dumps, loads
from typing import Any
from urllib.request import Request, urlopen


READ_ONLY_METHODS = {
    "eth_chainId",
    "eth_blockNumber",
    "eth_getBlockByNumber",
    "eth_getTransactionReceipt",
    "eth_getLogs",
    "eth_getCode",
    "eth_call",
}


@dataclass(frozen=True)
class RpcClient:
    endpoint: str
    timeout_seconds: float = 20.0

    def call(self, method: str, params: list[Any]) -> Any:
        if method not in READ_ONLY_METHODS:
            raise PermissionError(f"RPC method not allowlisted for data pipeline: {method}")
        body = dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }).encode("utf-8")
        req = Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=self.timeout_seconds) as response:
            payload = loads(response.read())
        if "error" in payload:
            raise RuntimeError(f"RPC error for {method}: {payload['error']}")
        return payload["result"]

    def verify_chain_id(self, expected: int = 4326) -> None:
        observed = int(self.call("eth_chainId", []), 16)
        if observed != expected:
            raise RuntimeError(f"wrong chain: expected {expected}, observed {observed}")
