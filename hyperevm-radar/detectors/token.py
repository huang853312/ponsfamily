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
    try:
        checksum = w3.to_checksum_address(address)

        total_raw = bytes(
            await w3.eth.call({
                "to": checksum,
                "data": "0x18160ddd"
            })
        )
        if len(total_raw) < 32:
            return None

        total_supply = int.from_bytes(total_raw[-32:], "big")

        decimals_raw = bytes(
            await w3.eth.call({
                "to": checksum,
                "data": "0x313ce567"
            })
        )
        if len(decimals_raw) < 32:
            return None

        decimals = int.from_bytes(decimals_raw[-32:], "big")
        if decimals > 36:
            return None

        symbol = ""
        name = ""

        try:
            symbol = _decode_text(bytes(
                await w3.eth.call({
                    "to": checksum,
                    "data": "0x95d89b41"
                })
            ))
        except Exception:
            pass

        try:
            name = _decode_text(bytes(
                await w3.eth.call({
                    "to": checksum,
                    "data": "0x06fdde03"
                })
            ))
        except Exception:
            pass

        if not symbol and not name:
            return None

        return {
            "address": address,
            "symbol": symbol or "UNKNOWN",
            "name": name or "UNKNOWN",
            "decimals": decimals,
            "total_supply": total_supply,
        }

    except Exception:
        return None
