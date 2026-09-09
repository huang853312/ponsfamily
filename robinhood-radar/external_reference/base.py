"""Non-authoritative market reference provider contract."""
from dataclasses import dataclass

NO_DATA="NO_DATA";UNAVAILABLE="UNAVAILABLE";UNVERIFIED="UNVERIFIED"

@dataclass(frozen=True)
class ReferenceResult:
    provider:str
    status:str=NO_DATA
    first_seen:str|None=None
    evidence:tuple[str,...]=()

class NoDataProvider:
    name="unknown"
    async def lookup(self,addresses):return ReferenceResult(self.name,NO_DATA,None,("no verified provider configured",))

