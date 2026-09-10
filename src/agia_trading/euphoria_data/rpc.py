from __future__ import annotations

import dataclasses
import json
import typing
import urllib.request

READ_ONLY_METHODS = {
    "eth_chainId",
    "eth_blockNumber",
    "eth_getBlockByNumber",
    "eth_getTransactionReceipt",
    "eth_getLogs",
    "eth_getCode",
    "eth_getStorageAt",
    "eth_call",
}


@dataclasses.dataclass(frozen=True)
class RpcClient:
    endpoint: str
    timeout_seconds: float = 20.0
    user_agent: str = "AGIA-TRADING-EUPHORIA-DATA/0.1 read-only-research"

    def call(self, method: str, params: list[typing.Any]) -> typing.Any:
        if method not in READ_ONLY_METHODS:
            raise PermissionError(f"RPC method not allowlisted for data pipeline: {method}")
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read())
        if "error" in payload:
            raise RuntimeError(f"RPC error for {method}: {payload['error']}")
        return payload["result"]

    def verify_chain_id(self, expected: int = 4326) -> None:
        observed = int(self.call("eth_chainId", []), 16)
        if observed != expected:
            raise RuntimeError(f"wrong chain: expected {expected}, observed {observed}")
