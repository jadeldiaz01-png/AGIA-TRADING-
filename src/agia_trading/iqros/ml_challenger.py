from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class ChallengerEvidence:
    model_id:str; oos_net_pnl:float; oos_sharpe:float; turnover:float; max_drawdown:float; calibrated:bool; leakage_pass:bool; cost_stress_pass:bool
def may_promote(champion:ChallengerEvidence,challenger:ChallengerEvidence)->tuple[bool,tuple[str,...]]:
    f=[]
    if not challenger.leakage_pass:f.append("NO_LEAKAGE")
    if not challenger.calibrated:f.append("CALIBRATION")
    if not challenger.cost_stress_pass:f.append("COST_STRESS")
    if challenger.oos_net_pnl<=champion.oos_net_pnl:f.append("INCREMENTAL_NET_PNL")
    if challenger.oos_sharpe<=champion.oos_sharpe:f.append("INCREMENTAL_SHARPE")
    if challenger.max_drawdown>champion.max_drawdown:f.append("DRAWDOWN")
    return not f,tuple(f)
