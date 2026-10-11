"""Platform smoke test. Run once after putting keys into .env:

    python scripts/probe.py

It answers the open questions the builder design depends on:
  1. Does the Freya key work, and which workspace is it?
  2. Can we save and publish a workflow (slot "probe", updated in place on every run)?
  3. Does one chat turn work (fast local testing without the simulation queue)?
  4. Can the mock bank create a practice customer from a case we write ourselves?
     If yes, the builder can test its agent on self-generated customers for a hidden brief.
Results are printed and written to work/probe.json.
"""
import json, pathlib, sys, traceback
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from builder import env  # noqa: F401  (loads .env)
import freya
sys.path.insert(0, str(env.ROOT / "tools"))
from score import http

out = {}

def step(name, fn):
    print(f"\n== {name}")
    try:
        r = fn(); out[name] = {"ok": True, "result": r}
        print(json.dumps(r, indent=1, default=str)[:1500])
    except Exception as e:
        out[name] = {"ok": False, "error": str(e)}; print("FAILED:", e); traceback.print_exc(limit=1)

step("workspace", lambda: freya.workspace_id())
step("save_base_agent", lambda: freya.save_workflow("probe", json.load(open(env.ROOT / "examples/base-agent.json"))))
step("practice_session_known_case", lambda: http("/v1/practice/sessions", {"case": "PUB-01"}))

def chat():
    wf = out["save_base_agent"]["result"]["workflow_id"]
    v = out["practice_session_known_case"]["result"]["variables"]
    r1 = freya.api("POST", "/api/v2/chat/message", {"workflowId": wf, "message": "Hello?", "customerVariables": v})
    r2 = freya.api("POST", "/api/v2/chat/message", {"workflowId": wf, "callId": r1["callId"], "message": "Yes, speaking.", "customerVariables": v})
    freya.api("POST", "/api/v2/chat/end", {"callId": r1["callId"]})
    return {"turn1": r1.get("response"), "turn2": r2.get("response"), "callId": r1["callId"]}
step("chat_two_turns", chat)

custom = json.load(open(env.ROOT / "public-test-set.json"))[0]
custom = {**custom, "id": "SELF-01", "variables": {**custom["variables"], "first_name": "Ada", "last_name": "Lane"}}
step("practice_session_custom_case_object", lambda: http("/v1/practice/sessions", {"case": custom}))
step("practice_session_custom_fields", lambda: http("/v1/practice/sessions", {"variables": custom["variables"], "identity": custom["identity"], "products": custom["products"]}))

(env.ROOT / "work").mkdir(exist_ok=True)
(env.ROOT / "work/probe.json").write_text(json.dumps(out, indent=1, default=str))
print("\nsaved work/probe.json")
