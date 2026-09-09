"""Evidence-only deployer history scoring."""
from dataclasses import dataclass

@dataclass(frozen=True)
class DeployerIntel:
    score: int|None
    status: str
    risk_flags: tuple[str,...]
    evidence: tuple[str,...]

def assess(*,contracts_created,erc20_count,silent_count,high_grade_clusters,max_same_template=0):
    if contracts_created<=1:return DeployerIntel(None,"UNKNOWN",(),("insufficient deployment history",))
    flags=[]
    if contracts_created>=10 and erc20_count/max(contracts_created,1)>=.8:flags.append("suspected spam deployer")
    if silent_count>=10:flags.append("mature launchpad spam history")
    if max_same_template>=5:flags.append("mass clone")
    score=max(0,min(15,7+min(high_grade_clusters,4)*2-len(flags)*5))
    return DeployerIntel(score,"ASSESSED",tuple(flags),(f"contracts_created={contracts_created}",f"A/S clusters={high_grade_clusters}"))
