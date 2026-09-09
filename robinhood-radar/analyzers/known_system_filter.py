"""Address-role index for confirmed mature launch systems."""
import json
from dataclasses import dataclass
from pathlib import Path

ALLOWED_ROLES={"factory","launcher","token_deployer","router","hook","locker","implementation","proxy","other"}

@dataclass(frozen=True)
class KnownMatch:
    system: str
    role: str
    address: str
    gmgn_confirmed: bool

class KnownSystemFilter:
    def __init__(self,path=None):
        path=path or Path(__file__).parents[1]/"data/gmgn_known_launchpads.json"
        payload=json.loads(Path(path).read_text(encoding="utf-8")); self.entries={}
        for system in payload["systems"]:
            for role,addresses in system.get("addresses",{}).items():
                if role not in ALLOWED_ROLES: raise ValueError(f"unsupported known-system role: {role}")
                for address in addresses:
                    self.entries[address.lower()]=KnownMatch(
                        system["name"],role,address.lower(),bool(system.get("gmgn_confirmed",False))
                    )
    def get(self,address): return self.entries.get(address.lower()) if address else None
    def match(self,*addresses): return next((m for address in addresses if (m:=self.get(address))),None)
    def is_known(self,address): return self.get(address) is not None
