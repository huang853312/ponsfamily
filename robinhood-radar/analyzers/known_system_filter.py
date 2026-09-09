import json
from pathlib import Path

class KnownSystemFilter:
    def __init__(self,path=None):
        path=path or Path(__file__).parents[1]/"data/gmgn_known_launchpads.json"
        payload=json.loads(Path(path).read_text())
        self.entries={a.lower():(s["name"],kind) for s in payload["systems"] for kind, values in s.get("addresses",{}).items() for a in values}
    def match(self,*addresses):
        return next((self.entries[a.lower()] for a in addresses if a and a.lower() in self.entries),None)

