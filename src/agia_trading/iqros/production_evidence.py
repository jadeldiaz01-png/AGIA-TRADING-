from __future__ import annotations
from dataclasses import dataclass,asdict
from hashlib import sha256
import json
@dataclass(frozen=True)
class EvidenceGate:
    name:str; status:str; evidence_ref:str|None=None
REQUIRED=(
"DATA_PROVENANCE","DATA_QUALITY","POINT_IN_TIME","NO_LEAKAGE","BASELINE_COMPARISON","OOS_NET_POSITIVE","WALK_FORWARD","COST_STRESS","PARAMETER_STABILITY","DSR","PBO","BOOTSTRAP","MONTE_CARLO","REGIME_STABILITY","FORWARD_PAPER","PAPER_RECONCILIATION","TESTNET","ADAPTER_CONTRACT","RISK_LIMITS","KILL_SWITCH","SLO","BACKUP_RESTORE","CHAOS","SUPPLY_CHAIN","SBOM","PROVENANCE_SIGNATURE","AGENT_SECURITY","SECRET_ISOLATION","HUMAN_APPROVAL")
def build(gates:list[EvidenceGate],strategy_hash:str,dataset_hashes:list[str],code_sha:str)->dict:
    by={g.name:g for g in gates}; missing=[n for n in REQUIRED if n not in by]; failed=[n for n in REQUIRED if n in by and by[n].status!="PASS"]; ready=not missing and not failed
    doc={"schema":"iqros.production-evidence.v2","strategy_hash":strategy_hash,"dataset_hashes":dataset_hashes,"code_sha":code_sha,"gates":[asdict(g) for g in gates],"missing_gates":missing,"failed_gates":failed,"production_ready":ready,"real_money_authorized":False,"authorization_note":"Evidence readiness never authorizes capital; explicit human approval and deployment-time capital policy remain separate."}
    raw=json.dumps(doc,sort_keys=True,separators=(",",":")).encode(); doc["manifest_sha256"]=sha256(raw).hexdigest(); return doc
