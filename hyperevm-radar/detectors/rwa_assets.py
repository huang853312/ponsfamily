# Confirmed HyperEVM Stock / RWA assets only.
# Never add an address here unless its identity has been verified.

KNOWN_RWA_ASSETS = {
}

def get_rwa_asset(address):
    if not address:
        return None
    return KNOWN_RWA_ASSETS.get(address.lower())

def is_rwa_asset(address):
    return get_rwa_asset(address) is not None
