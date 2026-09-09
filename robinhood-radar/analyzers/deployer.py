"""Optional bounded callTracer ancestry. Failure is data, never fabricated ancestry."""
import asyncio
class DeployerTracer:
    def __init__(self,enabled=False,max_concurrency=1,rpc_limiter=None): self.enabled,self._semaphore,self.rpc_limiter=enabled,asyncio.Semaphore(max_concurrency),rpc_limiter
    async def trace(self,w3,tx_hash):
        if not self.enabled: return {"status":"disabled","creations":[]}
        async with self._semaphore:
            return await self._trace(w3,tx_hash)
    async def _trace(self,w3,tx_hash):
        try:
            request=w3.provider.make_request("debug_traceTransaction",[tx_hash,{"tracer":"callTracer","timeout":"5s"}])
            tree=await self.rpc_limiter.call(request) if self.rpc_limiter else await request
            if tree.get("error"): return {"status":"unavailable","error":str(tree["error"]),"creations":[]}
            creations=[]
            def visit(node,parent=None):
                if node.get("type","").upper() in {"CREATE","CREATE2"} and node.get("to"):
                    creations.append({"address":node["to"].lower(),"creator":(node.get("from") or parent),"type":node["type"].upper()})
                for child in node.get("calls",[]): visit(child,node.get("to") or node.get("from") or parent)
            visit(tree.get("result",{})); return {"status":"available","creations":creations}
        except Exception as exc:
            return {"status":"unavailable","error":str(exc),"creations":[]}
