# AGIA TRADING

Institutional-grade quantitative research and data-certification platform.

## Safety and authorization boundary

- `MAX_AUTONOMOUS_CAPITAL_USD = 0`
- `EXECUTION_ENABLED_BY_DEFAULT = false`
- No live, pilot, paper, or testnet authorization is implied by repository code.
- LLM outputs may explain evidence; they cannot override deterministic certifiers.
- No wallet private keys, seed phrases, signing keys, or withdrawal capabilities belong in the data pipeline.

## First gate: EUPHORIA-DATA-001

Pipeline: source registry -> raw extraction -> canonical normalization -> reconciliation -> deterministic freeze -> `dataset_sha256` -> signed evidence bundle.

Certification requires `unresolved_count == 0`. Until then, strategy research and all execution modes remain unauthorized.

## Target progression

`DATA_NOT_CERTIFIED -> EUPHORIA_DATA_001_CERTIFIED -> RESEARCH_AUTHORIZED -> FROZEN_HOLDOUT_CANDIDATE -> SHADOW -> PAPER/TESTNET -> LIMITED_PILOT -> LIVE`

Each transition requires machine-verifiable evidence and, for financial execution, explicit human authorization.
