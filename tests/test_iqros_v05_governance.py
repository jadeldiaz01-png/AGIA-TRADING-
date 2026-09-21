from agia_trading.iqros.timebase import periods_per_year
from agia_trading.iqros.agent_policy import AgentAuthorityPolicy,AgentAction
from agia_trading.iqros.production_evidence import EvidenceGate,REQUIRED,build
from agia_trading.iqros.ml_challenger import ChallengerEvidence,may_promote
def test_crypto_6h_annualization(): assert periods_per_year("6h")==1460
def test_llm_is_advisory_only(): assert AgentAuthorityPolicy().authorize(AgentAction("llm","PLACE_ORDER","PAPER",places_order=True))[0] is False
def test_manifest_is_fail_closed_and_never_self_authorizes_capital():
    assert build([],"s",[],"c")["production_ready"] is False
    m=build([EvidenceGate(x,"PASS","e") for x in REQUIRED],"s",["d"],"c"); assert m["production_ready"] is True and m["real_money_authorized"] is False
def test_challenger_requires_incremental_economic_value():
    c=ChallengerEvidence("ATVF",10,1,2,.1,True,True,True); x=ChallengerEvidence("XGB",9,1.2,2,.09,True,True,True); ok,why=may_promote(c,x); assert not ok and "INCREMENTAL_NET_PNL" in why
