"""Static checks of a workflow, from the rules in docs/workflow-format.md and docs/tools-and-code.md."""
import json


def _walk(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, x


def check(wf):
    out = []
    nodes = wf.get("nodes", {})
    if wf.get("initial_node") not in nodes:
        out.append("initial_node missing")
    for path, v in _walk(wf):
        if v is None:
            out.append(f"null value at {path}")
    for name, n in nodes.items():
        t = n.get("node_type")
        if n.get("name") != name:
            out.append(f"{name}: name differs from key")
        for tr in n.get("transitions", []):
            if tr["target_node"] not in nodes and tr["target_node"] != "__previous__":
                out.append(f"{name}: transition to unknown node {tr['target_node']}")
        if t != "conversation" and n.get("tools"):
            out.append(f"{name}: tools on a {t} node are dropped by the platform")
        if t == "conversation" and n.get("pre_actions"):
            out.append(f"{name}: pre_actions on a conversation node are dropped by the platform")
        if n.get("passthrough"):
            trs = n.get("transitions", [])
            if not trs or trs[-1].get("condition", {}).get("operator") != "always":
                out.append(f"{name}: passthrough node must end with an always transition")
        if t == "end_call" and not n.get("terminal"):
            out.append(f"{name}: end_call without terminal")
        for f in n.get("pre_actions", []) + n.get("tools", []):
            fn = f.get("function", {})
            if fn.get("handler") == "__function__:code_exec":
                code = fn["parameters"]["properties"]["code"]["default"]
                if "{{" in code or "}}" in code:
                    out.append(f"{name}: code contains double braces")
                try:
                    compile(code, name, "exec")
                except SyntaxError as e:
                    out.append(f"{name}: code does not compile: {e}")
            for hk in ("pre_process_code", "post_process_code"):
                if f.get(hk):
                    try:
                        compile(f[hk], name, "exec")
                    except SyntaxError as e:
                        out.append(f"{name}: {fn.get('name')} {hk} does not compile: {e}")
    reach, todo = set(), [wf.get("initial_node")]
    while todo:
        k = todo.pop()
        if k in reach or k not in nodes:
            continue
        reach.add(k)
        todo += [t["target_node"] for t in nodes[k].get("transitions", [])]
    for k in nodes:
        if k not in reach:
            out.append(f"{k}: unreachable")
    if not any(n.get("node_type") == "end_call" for n in nodes.values()):
        out.append("no end_call node")
    json.dumps(wf)
    return out
