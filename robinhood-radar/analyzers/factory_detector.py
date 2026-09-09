def describe(factory_known: bool, child_count: int) -> str:
    if not factory_known and child_count > 1: return "未知 Factory / 疑似新协议体系"
    return "Known Factory" if factory_known else "Unknown"
