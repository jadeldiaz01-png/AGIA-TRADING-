# P0-EUPHORIA-ACCOUNTING-001

## Purpose

Reconstruct Euphoria transaction-level cashflow and oracle transport evidence without inferring economic semantics that have not been demonstrated.

## Authoritative RedStone layout basis

The parser follows the current RedStone EVM connector constants and backward calldata extraction model:

- signature: 65 bytes
- timestamp: 6 bytes
- data package count: 2 bytes
- data points count: 3 bytes
- data point value-size field: 4 bytes
- feed ID: 32 bytes
- unsigned metadata size: 3 bytes
- RedStone marker: 9 bytes, `0x000002ed57011e0000`

References:

- `redstone-finance/redstone-oracles-monorepo/packages/evm-connector/contracts/core/RedstoneConstants.sol`
- `redstone-finance/redstone-oracles-monorepo/packages/evm-connector/contracts/core/CalldataExtractor.sol`
- RedStone's Euphoria/Bolt publication documenting `ETH`, `ETH_HIGH`, and `ETH_LOW` as signed Euphoria feeds.

## Evidence collected

For recent successful calls to the two observed Euphoria selectors, the collector records:

- full transaction hash and block;
- business calldata separated from the RedStone suffix when present;
- data package count, feed IDs, raw values, timestamps, signatures, and signed-message Keccak hashes;
- USDm ERC-20 transfers into and out of the Euphoria proxy;
- verified `BalanceUpdate(address,uint256)` log layout;
- full topics/data for the two still-unknown Euphoria events;
- transaction-level proxy net USDm cashflow.

Raw oracle values are deliberately not assigned decimals unless independently verified. A 65-byte signature is preserved but is not marked cryptographically authorised until Euphoria's signer policy/data-service identity is independently established.

## Fail-closed certification

This gate remains `PARTIAL_EVIDENCE` while any of the following is unresolved:

- selector economic roles;
- economic role of `BalanceUpdate`;
- names/roles of `0x90cacdb...` and `0x9f039a0...`;
- RedStone authorised-signer/data-service policy;
- lifecycle linkage across transactions;
- gross payout formula;
- protocol fee formula;
- deterministic net payout/PnL identity.

`P0-EUPHORIA-BACKFILL-001` remains blocked until `unresolved_count == 0`. This gate cannot authorize paper, testnet, pilot, live execution, or autonomous capital.
