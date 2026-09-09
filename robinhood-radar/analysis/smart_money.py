"""Wallet reputation defaults to UNKNOWN until sufficient stored history exists."""
from dataclasses import dataclass

@dataclass(frozen=True)
class SmartMoneyResult:
    score:str="NO_DATA"
    confirmation:bool=False
    high_quality_wallets:int=0
    evidence:tuple[str,...]=("insufficient wallet performance history",)

def evaluate(profiles):
    high=sum(1 for profile in profiles if profile.get("status")=="HIGH_QUALITY")
    return SmartMoneyResult(str(min(10,high*2)),high>=2,high,(f"HIGH_QUALITY wallets={high}",)) if high else SmartMoneyResult()

