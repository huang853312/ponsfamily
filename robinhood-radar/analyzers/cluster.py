CORE={"Factory","Router","Vault","Oracle","Hook","Lending","AMM","Aggregator","Settlement","Registry","Proxy","Implementation"}

def score(labels, known_match, novel_template):
    core=sorted(set(labels)&CORE)
    if len(core)>=3: return "S", "同一部署者出现至少 3 类独立核心合约: "+", ".join(core)
    if core: return "A", "发现核心合约类型: "+", ".join(core)
    if known_match: return "SILENT", "命中 GMGN 已知成熟发行体系，且未发现独立协议结构"
    if novel_template: return "B", "独立新池，且字节码模板首次出现"
    return "B", "普通独立新 Pool；未命中已知发行体系"

