"""Distinguish explicit EVM execution failures from unavailable RPC reads."""
import ast

from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3RPCError


def is_contract_execution_failure(error):
    if isinstance(error, (BadFunctionCallOutput, ContractLogicError)):
        return True
    if not isinstance(error, Web3RPCError):
        return False
    response = getattr(error, 'rpc_response', None)
    details = response.get('error') if isinstance(response, dict) else None
    if not isinstance(details, dict):
        try:
            details = ast.literal_eval(str(error))
        except (ValueError, SyntaxError, TypeError):
            return False
    # Captured HyperEVM response: code -32003, EVM error: InvalidFEOpcode.
    # Match both fields; never turn rate limits or unknown node errors into empty data.
    return isinstance(details, dict) and details.get('code') == -32003 and details.get('message') in {
        'EVM error: InvalidFEOpcode', 'EVM error: InvalidOpcode',
    }
