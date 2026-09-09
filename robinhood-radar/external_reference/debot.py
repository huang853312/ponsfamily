"""Future DeBot reference adapter contract.

No stable, verified API is configured in V1. Implementations may add explanatory
evidence only; they must never discover, silence, downgrade, or directly upgrade a
cluster.
"""
from external_reference.base import NoDataProvider

class DeBotReferenceProvider(NoDataProvider):
    name="debot"
