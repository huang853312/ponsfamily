import asyncio

from web3.exceptions import BadFunctionCallOutput, ContractLogicError


class TokenMetadataUnavailable(RuntimeError):
    """A failed RPC read must be retried, not classified as a non-token."""


async def _call(w3, checksum, selector):
    try:
        return bytes(await asyncio.wait_for(
            w3.eth.call({'to': checksum, 'data': selector}), timeout=10))
    except (BadFunctionCallOutput, ContractLogicError):
        # The contract deterministically does not support this ERC-20 method.
        return b''
    except Exception as exc:
        raise TokenMetadataUnavailable(type(exc).__name__) from exc


def _decode_text(data: bytes):
    if not data:
        return ""

    try:
        if len(data) == 32:
            return data.rstrip(b"\x00").decode("utf-8", errors="ignore").strip()

        if len(data) >= 96:
            offset = int.from_bytes(data[:32], "big")
            if offset + 32 <= len(data):
                length = int.from_bytes(data[offset:offset + 32], "big")
                raw = data[offset + 32:offset + 32 + length]
                return raw.decode("utf-8", errors="ignore").strip()
    except Exception:
        pass

    return ""


async def detect_erc20(w3, address):
    checksum = w3.to_checksum_address(address)
    total_raw = await _call(w3, checksum, '0x18160ddd')
    if len(total_raw) < 32:
        return None
    total_supply = int.from_bytes(total_raw[-32:], 'big')
    decimals_raw = await _call(w3, checksum, '0x313ce567')
    if len(decimals_raw) < 32:
        return None
    decimals = int.from_bytes(decimals_raw[-32:], 'big')
    if decimals > 36:
        return None
    symbol = _decode_text(await _call(w3, checksum, '0x95d89b41'))
    name = _decode_text(await _call(w3, checksum, '0x06fdde03'))
    if not symbol and not name:
        return None
    return {'address': address, 'symbol': symbol or 'UNKNOWN', 'name': name or 'UNKNOWN',
            'decimals': decimals, 'total_supply': total_supply}
