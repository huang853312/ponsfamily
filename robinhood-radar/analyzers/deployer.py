"""Deployment ancestry extension point.

Standard JSON-RPC cannot enumerate CREATE/CREATE2 internals. Trace-capable adapters can
implement this interface later without changing the monitor or scoring pipeline.
"""
class DeployerTracer:
    async def related_contracts(self, tx_hash: str):
        return []

