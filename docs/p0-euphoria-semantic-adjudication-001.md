# P0-EUPHORIA-SEMANTIC-ADJUDICATION-001

This gate converts observed EVM selectors/topics into candidate human-readable semantics without allowing reverse-lookup databases or probabilistic interpretation to become authority.

## Evidence hierarchy

1. Exact on-chain selector/topic and bytecode presence.
2. Exact calldata/log indexed/data layout.
3. Independent signature databases as candidate generators only.
4. Official protocol/provider documentation.
5. Repeated behavioral consistency across transactions and upgrade eras.
6. Deterministic accounting reconciliation.

A semantic label is VERIFIED only when all applicable layers agree. A single 4byte/OpenChain match is not sufficient because selector collisions and incorrect crowd-sourced entries are possible.

## Current boundaries

- `0x21d5c9bb` and `0x93ca625d` remain semantically unresolved pending ABI-shape/behavioral proof.
- The three Euphoria-specific topic0 values remain unresolved pending indexed/data-layout proof.
- RedStone documentation independently establishes the Euphoria oracle feed family `ETH`, `ETH_HIGH`, `ETH_LOW`, but does not by itself establish the exact on-chain dependency graph.
- USDm inflows/outflows observed in receipts are accounting evidence, not sufficient alone to identify position/open/settle/fee semantics.
- `P0-EUPHORIA-CONTRACTS-001` remains `PARTIAL_EVIDENCE`.
- No research result from this gate authorizes paper, testnet, pilot, live trading, signing, withdrawal, or autonomous capital.
