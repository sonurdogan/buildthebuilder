"""Extract a spec from one brief (file or folder) and compare its key values with a reference spec.

    python scripts/compare_specs.py dev-briefs/raw-a work/ours-a/spec.json
"""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from builder import env, extract, trace  # noqa: F401

src, ref_path = sys.argv[1], sys.argv[2]
work = env.ROOT / "work" / ("extract-" + pathlib.Path(src).name)
work.mkdir(parents=True, exist_ok=True)
tr = trace.start("extract-" + pathlib.Path(src).name)
bt, api = extract.read_inputs([src], str(env.ROOT / "tools/openapi.json"))
rules, spec = extract.extract(bt, api, work)
spec, changes, missing = extract.reconcile(bt, rules, spec, work)
tr.end()
ref = json.load(open(ref_path, encoding="utf-8"))


def key(s):
    return {
        "holidays": sorted(s["calendar"]["holidays"]),
        "weekdays": s["calendar"]["business_weekdays"],
        "bands": [(b.get("min"), b.get("max"), b.get("deadline_business_days_after_today"), b.get("persuasion_attempts")) for b in s["bands"]],
        "identity": [q["field"] for q in s["identity"]["questions"]],
        "max_wrong": s["identity"].get("max_wrong_total"),
        "callback": s["third_party"].get("callback_window"),
        "outcomes": sorted((o["outcome"], o.get("reason") or "") for o in s["closing"]["outcomes"]),
        "stages": sorted((o["outcome"], o.get("reason") or "", tuple(sorted(o.get("stages") or []))) for o in s["closing"]["outcomes"]),
        "after_success": s["confirmation"].get("after_success_say"),
        "floor": s["amounts"].get("floor_field"),
        "retries": (s["confirmation"].get("on_failure") or {}).get("retries"),
        "multi": bool(s.get("multi_product")),
    }


a, b = key(spec), key(ref)
for k in a:
    print(("SAME " if a[k] == b[k] else "DIFF ") + k + ("" if a[k] == b[k] else f"\n   got: {a[k]}\n   ref: {b[k]}"))
print("overrides:", json.dumps(spec.get("overrides"), ensure_ascii=False)[:800])
print("open questions:", spec.get("open_questions"))
