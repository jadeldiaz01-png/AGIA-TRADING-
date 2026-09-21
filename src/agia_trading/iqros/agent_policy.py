from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class AgentAction:
    actor:str; action:str; environment:str; mutates_risk:bool=False; places_order:bool=False; changes_secret:bool=False
class AgentAuthorityPolicy:
    FORBIDDEN={"PLACE_ORDER","CANCEL_ORDER","SET_RISK_LIMIT","PROMOTE_LIVE","ROTATE_SECRET","WITHDRAW","TRANSFER"}
    def authorize(self,req:AgentAction)->tuple[bool,str]:
        if req.actor in {"llm","agent","multimodal_agent"} and (req.action in self.FORBIDDEN or req.places_order or req.mutates_risk or req.changes_secret):
            return False,"AGENT_ADVISORY_ONLY"
        if req.environment in {"LIVE_PILOT","LIMITED_PRODUCTION","PRODUCTION"} and req.actor!="deterministic_control_plane":
            return False,"CONTROL_PLANE_REQUIRED"
        return True,"AUTHORIZED"
