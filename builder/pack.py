"""Pack mode: the input is a folder of raw bank material (mails, attachments, documents, meeting notes,
test data) instead of a brief plus an OpenAPI file. Nothing here knows a case: every name, path, field,
rule and sentence comes from the folder.

    read_folder(folder)          all readable text of the folder, one block per file
    derive_api(material)         the bank's services as a list of operations (path, method, params, responses)
    build_tools(api, base_url)   Freya api_call tools for these operations
"""
import io, json, pathlib, re, zipfile

from . import llm, trace

TEXT_EXT = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".eml", ".html", ".htm", ".xml", ".svg",
            ".mmd", ".puml", ".js", ".ts", ".toml", ".ini", ".log", ".rst", ".sql", ".jsonc", ".mermaid", ".drawio"}
SKIP_DIRS = {"node_modules", ".git", ".wrangler", "__pycache__"}


def _docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return re.sub(r"<[^>]+>", "", xml)


def _xlsx_text(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            shared = [re.sub(r"<[^>]+>", "", s) for s in re.findall(r"<si>(.*?)</si>", sx, re.S)]
        out = []
        for n in sorted(x for x in names if x.startswith("xl/worksheets/sheet")):
            sx = z.read(n).decode("utf-8", "replace")
            for row in re.findall(r"<row[^>]*>(.*?)</row>", sx, re.S):
                cells = []
                for attrs, body in re.findall(r"<c([^>]*)>(.*?)</c>", row, re.S):
                    v = re.search(r"<v>(.*?)</v>", body, re.S)
                    t = re.search(r"<t[^>]*>(.*?)</t>", body, re.S)
                    val = v.group(1) if v else (t.group(1) if t else "")
                    if 't="s"' in attrs and val.isdigit() and int(val) < len(shared):
                        val = shared[int(val)]
                    cells.append(val)
                out.append("\t".join(cells))
            out.append("")
        return "\n".join(out)


def _pdf_text(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return "(pdf: pypdf not installed)"
    return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)


def read_file(f):
    ext = f.suffix.lower()
    try:
        if ext == ".docx":
            return _docx_text(f)
        if ext == ".xlsx":
            return _xlsx_text(f)
        if ext == ".pdf":
            return _pdf_text(f)
        if ext in TEXT_EXT or ext == "":
            return f.read_text(encoding="utf-8", errors="replace")
        raw = f.read_bytes()[:4096]
        if b"\x00" not in raw:
            return f.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"(could not read: {e})"
    return None


BIG = 40000      # a file longer than this goes into prompts as its head only (large data files)
HEAD = 14000


def read_folder(folder):
    """Returns (text, files). Each file becomes a block headed by its path relative to the folder.
    Large files (test data dumps) are cut to their head; the full data reaches the mock verbatim
    through data_files()."""
    folder = pathlib.Path(folder)
    files = sorted(x for x in folder.rglob("*") if x.is_file() and not (SKIP_DIRS & set(x.parts)))
    parts, listed = [], []
    for f in files:
        rel = f.relative_to(folder).as_posix()
        txt = read_file(f)
        if txt is None:
            parts.append(f"=== FILE: {rel} === (binary file, {f.stat().st_size} bytes, not readable as text)")
        elif len(txt) > BIG:
            parts.append(f"=== FILE: {rel} === (large file, {len(txt)} chars; first {HEAD} shown)\n{txt[:HEAD]}\n[... cut ...]")
        else:
            parts.append(f"=== FILE: {rel} ===\n{txt.strip()}")
        listed.append(rel)
    return "\n\n".join(parts), listed


def data_files(folder):
    """Every JSON file of the folder, parsed: {relative path: data}. The mock embeds them verbatim."""
    folder = pathlib.Path(folder)
    out = {}
    for f in sorted(folder.rglob("*.json")):
        if SKIP_DIRS & set(f.parts):
            continue
        try:
            out[f.relative_to(folder).as_posix()] = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return out


SYSTEM = """You read raw material a bank sent for a phone-assistant project: mails, attachments, analysis
documents in several versions, integration documents, data models, meeting notes, FAQs, test data.
The documents were written months apart and do not always agree. Newer and more authoritative material
wins: the latest version of a document over earlier ones, a later mail or meeting decision over an older
document, the integration document over a drawing. Be exact: copy paths, field names, enum values,
status codes, numbers and required sentences verbatim. Never invent what the material does not give."""


API_FORMAT = """{
 "auth_headers": {"Header-Name": "value"},          // fixed headers every request needs, {} if none
 "body_wrapper": "",                                // key the service fields go under when the contract wraps them
                                                    // in an envelope (e.g. "data"); "" when fields are top level
 "body_extra": {},                                  // other fixed top-level keys of the request envelope, e.g.
                                                    // {"call": {"lang": "tr"}}; {} if none
 "operations": [{
   "tool": "snake_case_name",                       // a short tool name for the phone agent, from the operation
   "method": "GET|POST|PUT|PATCH|DELETE",
   "path": "/exact/path/{pathParam}",               // the full path after the host, exactly as in the integration
                                                    // document (keep a base path such as /api/v2 and dots in names)
   "purpose": "what it does, one sentence",
   "headers": {"Header-Name": "value"},             // extra fixed headers of this operation
   "params": [{
     "name": "exactFieldName", "in": "path|query|body", "type": "string|integer|number|boolean|array|object",
     "required": true, "enum": ["..."], "format": "date|date-time|email|...", "description": "meaning and format",
     "fill": "call_variable|model|fixed", "value": "name of the call variable, or the fixed value"
     // in = body: a service field (inside body_wrapper when there is one)
   }],
   "responses": [{"status": 200, "when": "when this answer comes", "body": {"exact": "example body"}}]
 }],
 "call_variables": [{"name": "customerNumber", "meaning": "..."}],
 "notes": ["conflicts you resolved and how"]
}"""


def derive_api(material):
    prompt = f"""<material>
{material}
</material>

Describe the bank services the phone assistant calls, exactly as the bank's integration material defines
them (the newest valid version where versions disagree). One operation per endpoint. Keep every path,
method, field name, field casing, enum value, header and HTTP status exactly as written. List every
documented response of each operation, success and error, with an exact example body.
`fill`: `call_variable` when the value comes from a variable the call starts with (say which), `fixed`
for constants the document fixes (channel codes, versions), `model` for what the assistant must choose
or ask during the call.
If the material names services but gives no HTTP paths (no integration document), use `POST` and the
service name with dots turned into slashes as the path (`a.b.c` -> `/a/b/c`), with a JSON body that holds
the fields the material and test data use to call it (same names and casing), and say so in `notes`.
Response bodies are then exactly the ones the test data lists.

Return one JSON object in this shape (comments are explanations, not part of the JSON):
{API_FORMAT}"""
    api = llm.ask_json(prompt, "derive the bank service operations from the integration material", system=SYSTEM)
    tr = trace.current()
    if tr:
        tr.event("api_derived", operations=[f"{o['method']} {o['path']} -> {o['tool']}" for o in api.get("operations", [])],
                 notes=api.get("notes"))
    return api


# --------------------------------------------------------------------------------------- Freya tools

PRE_HOOK = """import json
try:
    from urllib.parse import quote, urlencode
    info = json.loads(INFO)
    b = config.get('body')
    if not isinstance(b, dict):
        b = dict()
    for k in list(b.keys()):
        v = b.get(k)
        if v is None or (isinstance(v, str) and (v.strip() == '' or v.strip().startswith('{'))):
            b.pop(k, None)
    url = str(config.get('url') or '')
    for name in info['path']:
        token = '{' + name + '}'
        if token in url:
            val = b.pop(name, None)
            if val is None:
                val = vars.get(name, '')
            url = url.replace(token, quote(str(val).strip(), safe=''))
    q = dict()
    for name in info['query']:
        if name in b:
            q[name] = b.pop(name)
    if info['method'] in ('GET', 'DELETE'):
        q.update(b)
        b = dict()
    if q:
        url = url + ('&' if '?' in url else '?') + urlencode(q)
    config['url'] = url
    if info['method'] in ('GET', 'DELETE'):
        config.pop('body', None)
    else:
        if info.get('wrap'):
            env = dict(info.get('extra') or dict())
            env[info['wrap']] = b
            b = env
        else:
            for k, v in (info.get('extra') or dict()).items():
                b.setdefault(k, v)
        config['body'] = b
except Exception:
    pass
"""

POST_HOOK = """import json
try:
    nxt = json.loads(NEXT)
    if isinstance(result, dict):
        result.pop('header', None)
        code = result.get('status_code')
        d = result.get('data')
        ok = isinstance(code, int) and 200 <= code < 300
        vars['TOOL_last_status'] = str(code)
        if isinstance(d, dict):
            d['what_to_do_next'] = nxt.get('ok' if ok else 'fail', '')
        elif isinstance(d, list):
            result['what_to_do_next'] = nxt.get('ok' if ok else 'fail', '')
        else:
            result['what_to_do_next'] = nxt.get('ok' if ok else 'fail', '')
except Exception:
    pass
"""


def _lit(s):
    # JSON stays valid with a space between braces; the engine would read double braces as a variable
    while "{{" in s or "}}" in s:
        s = s.replace("{{", "{ {").replace("}}", "} }")
    r = repr(s)
    assert "{{" not in r and "}}" not in r
    return r


def build_tool(op, base_url, auth_headers, texts, wrapper="", extra=None):
    """One api_call tool. `texts` = {"description", "params": {name: description}, "ok", "fail"} written by
    the builder's model for this operation."""
    props, required = {}, []
    path_names = [p["name"] for p in op["params"] if p.get("in") == "path"]
    query_names = [p["name"] for p in op["params"] if p.get("in") == "query"]
    for p in op["params"]:
        typ = p.get("type") or "string"
        if typ not in ("string", "integer", "number", "boolean"):
            typ = "string" if typ != "array" else "array"
        leaf = {"type": typ}
        if typ == "array":
            leaf["items"] = {"type": "string"}
        fill = p.get("fill")
        if fill == "call_variable" and p.get("value"):
            leaf["default"] = "{{" + str(p["value"]) + "}}"
        elif fill == "fixed" and p.get("value") is not None:
            leaf["default"] = p["value"] if typ in ("integer", "number", "boolean") else str(p["value"])
        else:
            desc = (texts.get("params") or {}).get(p["name"]) or p.get("description") or p["name"]
            if p.get("enum"):
                leaf["enum"] = p["enum"]
                desc += " One of: " + ", ".join(map(str, p["enum"])) + "."
            if p.get("format") == "date":
                desc += " Format YYYY-MM-DD."
            leaf["description"] = desc
            leaf["default"] = "{{" + p["name"] + "}}"
            if p.get("required"):
                required.append(p["name"])
        props[p["name"]] = leaf
    headers = {"Content-Type": {"type": "string", "default": "application/json"}}
    for k, v in {**(auth_headers or {}), **(op.get("headers") or {})}.items():
        headers[k] = {"type": "string", "default": str(v)}
    info = json.dumps({"path": path_names, "query": query_names, "method": op["method"].upper(),
                       "wrap": wrapper or "", "extra": extra or {}}, ensure_ascii=False)
    nxt = json.dumps({"ok": texts.get("ok", ""), "fail": texts.get("fail", "")}, ensure_ascii=False)
    t = {
        "type": "function",
        "config": {"timeoutMs": 20000, "maxRetries": 1 if op["method"].upper() == "GET" else 0},
        "function": {
            "name": op["tool"],
            "handler": "__function__:api_call",
            "description": texts.get("description") or op.get("purpose", ""),
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string", "default": base_url.rstrip("/") + op["path"]},
                "method": {"type": "string", "default": op["method"].upper()},
                "headers": {"type": "object", "properties": headers},
                "body": {"type": "object", "properties": props, "required": required},
            }},
        },
        "pre_process_code": PRE_HOOK.replace("INFO", _lit(info)),
        "post_process_code": POST_HOOK.replace("NEXT", _lit(nxt)).replace("TOOL", op["tool"]),
    }
    for code in (t["pre_process_code"], t["post_process_code"]):
        assert "{{" not in code and "}}" not in code
        compile(code, "hook", "exec")
    return t
