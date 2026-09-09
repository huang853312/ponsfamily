"""Transparent opportunity score kept independent from structural level."""
from dataclasses import dataclass

NO_DATA="NO_DATA";UNKNOWN="UNKNOWN"
LIMITS={"protocol_structure":25,"code_novelty":15,"dev_quality":15,"capital_growth":20,"real_usage":10,"smart_money":10,"earlyness":5}

@dataclass(frozen=True)
class OpportunityScore:
    components:dict
    total:int
    available_max:int
    confidence:str
    risk_flags:tuple
    evidence:tuple

def calculate(components,risk_flags=(),evidence=()):
    clean={}
    for key,limit in LIMITS.items():
        value=components.get(key,NO_DATA)
        if isinstance(value,int):value=max(0,min(limit,value))
        elif value not in {NO_DATA,UNKNOWN}:raise ValueError(f"invalid {key} score")
        clean[key]=value
    numeric=[v for v in clean.values() if isinstance(v,int)];available=sum(LIMITS[k] for k,v in clean.items() if isinstance(v,int))
    confidence="HIGH" if available>=85 else "MEDIUM" if available>=55 else "LOW"
    return OpportunityScore(clean,sum(numeric),available,confidence,tuple(risk_flags),tuple(evidence))
