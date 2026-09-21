# IQROS v0.5 — Production Evidence Architecture

IQROS is integrated as an isolated subsystem so existing Euphoria evidence remains immutable.

## Authority planes
1. **Deterministic control plane** — risk limits, OMS, reconciliation, kill switch, promotion gates.
2. **Statistical plane** — ATVF champion plus ML/DL challengers. A challenger must add OOS net economic value after costs and pass leakage/calibration/robustness gates.
3. **Agentic plane** — LLM/multimodal agents may research, explain, triage incidents and propose changes. They cannot place/cancel orders, change risk, rotate secrets, transfer funds, or promote live.

## 6h correction
Crypto trades 24/7. Six-hour bars use 4*365 = 1460 periods/year, not 252. Historical 6h metrics annualized with 252 are not production evidence.

## Mandatory evidence
Production readiness requires all gates in production_evidence.REQUIRED, including point-in-time data, no leakage, baseline comparison, OOS, walk-forward, cost stress, parameter stability, DSR/PBO, bootstrap/Monte Carlo, regime stability, forward PAPER, reconciliation, TESTNET, risk/kill switch, SLO/DR/chaos, SBOM/provenance signature, agent security, secret isolation and human approval.

