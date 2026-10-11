"""Steps 1-3: read the brief files, turn them into rules.json and spec.json, then check the spec.

Speed: model time is output tokens. The rule list and three parts of the spec are written by four
parallel calls. The check returns only corrections (patches), not the whole spec again."""
import json, pathlib, re

from . import llm, trace
from .env import ROOT

SCHEMA = (ROOT / "builder" / "spec_schema.md").read_text(encoding="utf-8")

SYSTEM = """You turn a bank's specification for an outbound phone agent into structured data.
The material can be a clean numbered specification or raw bank material: emails, policy extracts,
memos that change earlier rules, code lists, tables. Read all of it. Later material overrides earlier
material when it says so or when it is clearly an amendment. Be exact: copy numbers, dates, names,
enum values and required sentences verbatim. Never invent a value the material does not give."""

PARTS = {
    "who_and_identity": ["bank_name", "language", "agent_identity", "call_variables", "now_variable", "opening",
                         "forbidden_before_verification", "third_party", "identity", "account"],
    "dates_amounts_promises": ["calendar", "bands", "promise_date", "amounts", "negotiation", "confirmation"],
    "intents_and_closing": ["intents", "multi_product", "closing", "tool_usage", "other_rules"],
}


def read_inputs(brief_paths, openapi_path):
    parts = []
    for p in brief_paths:
        p = pathlib.Path(p)
        files = sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [p]
        for f in files:
            parts.append(f"=== SOURCE {len(parts) + 1}: {f.name} ===\n{f.read_text(encoding='utf-8', errors='replace')}")
    api = pathlib.Path(openapi_path).read_text(encoding="utf-8")
    return "\n\n".join(parts), api


def _context(brief_text, api_text):
    return f"<material>\n{brief_text}\n</material>\n\n<tool_api>\n{api_text}\n</tool_api>\n\n<spec_schema>\n{SCHEMA}\n</spec_schema>\n\n"


def extract(brief_text, api_text, work):
    tr = trace.current()
    ctx = _context(brief_text, api_text)
    rules_prompt = ctx + """List every atomic rule of the material: one object per rule with `id` (R1, R2, ...), `text`
(the rule in one plain sentence with its exact values) and `src` (rule number, section, file name or a short
quote). Split compound rules. Include definitions, table rows, calendars, required sentences and closing
outcomes. If a later source changes a rule, list only the final version and say so in `text`.
Return {"rules": [...]}."""

    def part_prompt(keys):
        return ctx + f"""Fill ONLY these top-level keys of the spec schema: {keys}.
Take tool names, field names and enum values from the tool API. Where the material is silent use null.
Also return "overrides" (changes later material made to earlier material that touch these keys) and
"open_questions" (gaps that touch these keys).
Return one JSON object with exactly the keys {keys + ["overrides", "open_questions"]}."""

    jobs = {"rules": lambda: llm.ask_json(rules_prompt, "list every atomic rule of the brief", system=SYSTEM)}
    for name, keys in PARTS.items():
        jobs[name] = (lambda keys=keys, name=name: llm.ask_json(part_prompt(keys), f"extract spec part: {name}", system=SYSTEM))
    res = llm.parallel(jobs)
    rules = res["rules"]["rules"]
    spec, overrides, questions = {}, [], []
    for name, keys in PARTS.items():
        part = res[name]
        for k in keys:
            spec[k] = part.get(k)
        overrides += part.get("overrides") or []
        questions += part.get("open_questions") or []
    spec["overrides"], spec["open_questions"] = overrides, questions
    (work / "rules.json").write_text(json.dumps(rules, indent=1, ensure_ascii=False), encoding="utf-8")
    (work / "spec.draft.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    if tr:
        tr.event("extracted", rules=len(rules), open_questions=questions)
    return rules, spec


_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def set_path(obj, path, value):
    """Set a value at a path such as `bands[1].deadline_business_days_after_today`. Creates missing dicts."""
    toks = [(m.group(1), m.group(2)) for m in _TOKEN.finditer(path)]
    cur = obj
    for i, (key, idx) in enumerate(toks):
        last = i == len(toks) - 1
        if idx is not None:
            idx = int(idx)
            while len(cur) <= idx:
                cur.append({})
            if last:
                cur[idx] = value
            else:
                cur = cur[idx]
        else:
            if last:
                cur[key] = value
            else:
                if not isinstance(cur.get(key), (dict, list)):
                    nxt = toks[i + 1]
                    cur[key] = [] if nxt[1] is not None else {}
                cur = cur[key]


def reconcile(brief_text, rules, spec, work):
    tr = trace.current()
    prompt = f"""<material>
{brief_text}
</material>

<rules>
{json.dumps(rules, ensure_ascii=False)}
</rules>

<spec>
{json.dumps(spec, indent=1, ensure_ascii=False)}
</spec>

<spec_schema>
{SCHEMA}
</spec_schema>

The spec was written in three parts by different people. Check it against the material and the rule list:
- Every rule must be represented in the spec (in its field, or in other_rules).
- Every value must match the material: numbers, dates, holidays, limits, order of questions, outcome and
  reason names, stages of each outcome, tool names, required sentences. The material wins over the rule list.
- Later material that changes earlier rules: the spec must hold the final value.
- The parts must agree with each other (same outcome names, same tool names, same band names).

Return ONLY the corrections, as one JSON object:
{{"patches": [{{"path": "bands[1].deadline_business_days_after_today", "value": 7, "why": "memo item 1"}}],
  "uncovered_rules": ["R12"]}}
`path` uses dots and [index]; `value` replaces what is there (give the whole object or list when you change
several fields of it, or append to a list by using the next free index). Empty patches when all is right."""
    out = llm.ask_json(prompt, "check the spec against every rule and return corrections")
    patches = out.get("patches") or []
    for p in patches:
        try:
            set_path(spec, p["path"], p["value"])
        except Exception as e:
            p["error"] = str(e)
    (work / "spec.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    (work / "patches.json").write_text(json.dumps(patches, indent=1, ensure_ascii=False), encoding="utf-8")
    missing = out.get("uncovered_rules") or []
    changes = [f"{p['path']}: {p.get('why', '')}" for p in patches]
    if tr:
        tr.event("reconciled", changes=changes, uncovered_rules=missing)
    return spec, changes, missing
