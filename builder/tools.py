"""Freya tool definitions generated from the brief's OpenAPI file. Nothing here knows a brief:
names, fields, enums and URLs all come from the file.

Every tool gets a post-hook that removes HTTP headers, records the result into call variables
(for routing and for later code), and adds `what_to_do_next` so the agent model always has a
next step after a tool result (docs/tools-and-code.md, "Tool results and the next turn")."""
import json, pathlib

WRITE_HINTS = ("create", "add", "schedule", "transfer", "close", "record", "book", "update", "cancel")


def load_api(path):
    api = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    base = api["servers"][0]["url"].rstrip("/")
    tools = {}
    for p, ops in api["paths"].items():
        op = ops.get("post") or ops.get("get")
        method = "POST" if "post" in ops else "GET"
        schema = (op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {}))
        resp = (op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {}))
        name = p.strip("/").replace("/", "_")
        tools[name] = {"name": name, "url": base + p, "method": method, "summary": op.get("summary", ""),
                       "fields": schema.get("properties", {}), "required": schema.get("required", []),
                       "response": resp.get("properties", {})}
    return tools


def _hook(tool_name, next_by_case):
    """Python post-hook. `next_by_case` maps 'ok' / 'fail' / 'error' to an instruction line.
    The hook never raises and never contains double braces."""
    msgs = json.dumps(next_by_case)
    return f"""import json
try:
    msgs = json.loads({msgs!r})
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            ok = d.get('ok') is True or str(d.get('ok')).lower() == 'true'
            vars['{tool_name}_last'] = 'ok' if ok else 'fail'
            d['what_to_do_next'] = msgs.get('ok' if ok else 'fail', '')
        else:
            vars['{tool_name}_last'] = 'error'
            result['what_to_do_next'] = msgs.get('error', '')
    else:
        vars['{tool_name}_last'] = 'error'
except Exception:
    pass
"""


def tool(spec, baked=None, model_fields=None, description="", next_by_case=None, hook=None, retries=None):
    """One api_call tool.

    baked:        {field: value or "{{var}}"} fixed by the workflow, hidden from the model
    model_fields: {field: description} the model fills (enum and type come from the API file)
    The customer_id-like fields that are call variables should be passed in `baked`."""
    baked = baked or {}
    model_fields = model_fields or {}
    props = {}
    for f, sch in spec["fields"].items():
        leaf = {"type": sch.get("type", "string")}
        if sch.get("format") == "date":
            leaf["description"] = "Date as YYYY-MM-DD."
        if "enum" in sch:
            leaf["enum"] = sch["enum"]
        if f in baked:
            leaf["default"] = baked[f]
        elif f in model_fields or f in spec["required"]:
            desc = model_fields.get(f) or sch.get("description") or f
            if "enum" in sch:
                desc += " One of: " + ", ".join(sch["enum"]) + "."
            if sch.get("format") == "date":
                desc += " Date as YYYY-MM-DD."
            leaf["description"] = desc
            leaf["default"] = "{{" + f + "}}"
        else:
            continue
        props[f] = leaf
    write = spec["name"].startswith(WRITE_HINTS)
    t = {
        "type": "function",
        "config": {"timeoutMs": 12000, "maxRetries": retries if retries is not None else (0 if write else 2)},
        "function": {
            "name": spec["name"],
            "handler": "__function__:api_call",
            "description": description or spec["summary"],
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string", "default": spec["url"]},
                "method": {"type": "string", "default": spec["method"]},
                "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                "body": {"type": "object", "properties": props,
                         "required": [f for f in spec["required"] if f in props and f not in baked]},
            }},
        },
    }
    code = hook if hook is not None else _hook(spec["name"], next_by_case or {
        "ok": "Done. Tell the customer the result in one short sentence, then continue.",
        "fail": "This did not work. Tell the customer honestly that it did not work. Never say it was done.",
        "error": "The service failed. Nothing was recorded. Tell the customer honestly."})
    if code:
        t["post_process_code"] = code
    return t
