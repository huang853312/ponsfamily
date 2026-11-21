class AdapterNotConfigured(RuntimeError): pass
class ExecutionAdapter:
    """Chain/DEX-specific implementation must be reviewed before live use."""
    def build_and_send(self, signal: dict, signer_key: str) -> dict:
        raise AdapterNotConfigured('No reviewed chain-specific transaction adapter installed; refusing unknown contract call')
def execute_live(signal: dict, signer_key: str, adapter: ExecutionAdapter) -> dict:
    if not signer_key: raise RuntimeError('Signer key is not configured')
    if not signal.get('router_verified') or not signal.get('adapter_verified'):
        raise RuntimeError('Unverified router/adapter; transaction refused')
    return adapter.build_and_send(signal,signer_key)
