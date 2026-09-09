"""Conservative infrastructure grading with explicit relationship requirements."""
RANK={"SILENT":0,"B":1,"A":2,"S":3}
CORE={"Factory","Router","Vault","Oracle","Hook","Lending","AMM","Aggregator","Settlement","Registry","Proxy","Implementation"}
STANDALONE_CORE={"Lending","Oracle","AMM","Aggregator","Settlement"}
STRONG_RELATIONS={"created_by","proxy_implementation","factory_ancestry"}

def grade(snapshot, known_context, known_addresses):
    independent=[m for m in snapshot["members"] if m["role"] in CORE and m["address"].lower() not in known_addresses]
    roles={m["role"] for m in independent}
    relations={r["relation"] for r in snapshot["relationships"]}
    strong=relations & STRONG_RELATIONS
    qualifying=roles & STANDALONE_CORE
    combinations=[]
    for required,name in [({"Factory","Router"},"Factory + Router"),({"Vault","Registry"},"Vault + Registry")]:
        if required<=roles and strong:combinations.append(name)
    if len(roles)>=3 and len(strong)>=2 and (qualifying or combinations):
        return "S",f"疑似完整核心协议集群：{', '.join(sorted(roles))}；关系证据：{', '.join(sorted(strong))}"
    if qualifying or combinations:
        evidence=set(qualifying)|set(combinations)
        return "A",f"可验证的独立核心协议证据：{', '.join(sorted(evidence))}"
    if known_context and known_context.gmgn_confirmed:
        return "SILENT",f"命中 GMGN-confirmed {known_context.system} {known_context.role} 路径，未观察到独立核心结构"
    return "B","新合约/Pool 已发现，但当前没有足够底层核心协议证据"

def novelty_score(features):
    weights={"deployer":15,"bytecode_hash":10,"template":10,"selector_set":10,"event_topic_set":5,"implementation":20,
             "Factory":15,"Router":12,"Hook":15,"Vault":12,"Oracle":12,"Implementation":20}
    return min(100,sum(weights.get(k,5) for k,v in features.items() if v))
