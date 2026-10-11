"""Local checks of the generated code snippets against the practice cases' expected dates."""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from builder import calendar_code as cc

spec = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "work/try-b/spec.json", encoding="utf-8"))
SRC = '''def derived(today, deadline, product):
    a1 = last_bd_of_week(today)
    if a1 <= today:
        a1 = last_bd_of_week(today + datetime.timedelta(days=7))
    return dict(attempt1_date=a1, attempt2_date=deadline)
'''
print("derived problems:", cc.check_derived(spec, SRC))
code = cc.prepare_code(spec, "products", "Your [phrase] has a past-due amount of [past_due] and is [days_late] days late.", SRC)
for case in json.load(open("public-test-set.json")):
    prods = [dict(p, product_id=f"p{i+1}") for i, p in enumerate(case["products"])]
    v = cc.run_local(code, {"now": case["variables"]["now"], "products": json.dumps(prods)})
    exp = [(p["product_id"], p["date"]) for p in case["expected"]["promises"]]
    print(case["id"], v["account_state"], "|", v["statement_text"][:120], "| expected:", exp)
print()
print(v["products_facts"])
