"""Step 4: spec + texts -> Freya workflow JSON. Deterministic: the same inputs give the same workflow.

Structure (code decides it, never the phone agent's model):
  setup (code)            all date facts from `now`
  opening (conversation)  fixed first line; who is on the line; third-party paths; may close
  id_<field> ...          one node per security question; exit only on the verify tool's result
  identity_failed         fixed line + close_call(not_verified/failed) by the engine
  get_account (api)       engine fetches products after verification
  prepare (code)          per-product facts: names, floors, bands, deadlines, valid dates, offers
  account_error           fixed line + close_call by the engine
  collection (conv)       negotiation, promises, notes, transfers; closes with close_call
  end                     hang up

`texts` holds everything worded by the builder's model (see builder/texts.py): system prompt,
fixed lines and the task message of each conversation node."""
import json

from . import calendar_code, tools as T

CONV = {"node_type": "conversation", "max_node_turns": 60}

# Code node after the engine-run retry K: was the promise recorded? Reads the mapped variable, the
# flattened result keys and the node's success counter, whichever the platform filled.
RETRY_CHECK = """try:
    ok = 'fail'
    for key in ('retry_ok_K', 'promise_retry_K_response.data.ok'):
        if str(vars.get(key)).lower() == 'true':
            ok = 'ok'
    for key in ('promise_retry_K_response.data.error',):
        v = str(vars.get(key) or '').lower()
        if 'already' in v:
            ok = 'ok'
    vars['retry_state_K'] = ok
except Exception:
    vars['retry_state_K'] = 'fail'
"""

# Pre-hook of the engine-run retry: fill any field whose placeholder did not resolve from the failed
# call's arguments, and normalise amount and date.
RETRY_PRE = """try:
    b = config.get('body')
    if isinstance(b, dict):
        for f in list(b.keys()):
            v = b.get(f)
            if v is None or str(v).strip() == '' or str(v).startswith('{'):
                b[f] = vars.get('TOOL_args.' + f)
        a = b.get('amount')
        if isinstance(a, str):
            b['amount'] = float(a.replace(',', '').strip())
        d = b.get('date')
        if isinstance(d, str):
            b['date'] = d.strip()[:10]
except Exception:
    pass
"""


def or_eq(var, *vals):
    return {"logic": "or", "conditions": [{"variable": var, "operator": "equals", "operand": v} for v in vals]}


def truthy(var):
    return or_eq(var, "true", "True")


def gt(var, n=0):
    return {"logic": "or", "conditions": [{"variable": var, "operator": "greater_than", "operand": n}]}


def always(target):
    return {"target_node": target, "description": "always", "condition": {"operator": "always"}}


def code_node(name, code, nxt, transitions=None):
    return {
        "name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": nxt,
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [{"type": "function", "function": {
            "name": "code_exec", "handler": "__function__:code_exec",
            "parameters": {"type": "object", "properties": {
                "code": {"type": "string", "default": code}, "timeout_ms": {"type": "number", "default": 15000}}}}}],
        "transitions": (transitions or []) + [always(nxt)],
    }


def hook(body):
    """Wrap a post-hook body. `d` is the API's JSON body (created if missing). Never raises."""
    lines = "".join("        " + l + "\n" for l in body.strip("\n").split("\n"))
    code = ("try:\n    if isinstance(result, dict):\n        result.pop('header', None)\n        d = result.get('data')\n"
            "        if not isinstance(d, dict):\n            d = dict()\n            result['data'] = d\n" + lines +
            "except Exception:\n    pass\n")
    assert "{{" not in code and "}}" not in code
    compile(code, "hook", "exec")
    return code


def pylit(s):
    """A Python string literal that never contains double braces."""
    return repr(s).replace("{{", "{ {").replace("}}", "} }")


class Compiler:
    def __init__(self, spec, api, texts):
        self.s, self.api, self.t = spec, api, texts
        self.cust_field = self._customer_field()
        self.close_spec = api[spec["closing"]["tool"]]
        self.vt = spec["identity"]["tool"]
        resp = api[self.vt]["response"]
        pick = lambda pred, default: next((k for k in resp if pred(k.lower())), default)
        self.r_all = pick(lambda k: "all" in k and "verif" in k, "all_verified")
        self.r_one = pick(lambda k: "verif" in k and "all" not in k, "verified")
        self.r_left = pick(lambda k: "left" in k or "remaining" in k, "wrong_answers_left")

    # ------------------------------------------------------------------ helpers
    def _customer_field(self):
        call_vars = {v["name"] for v in self.s.get("call_variables", [])}
        for spec in self.api.values():
            for f in spec["fields"]:
                if f in call_vars:
                    return f
        return "customer_id"

    def baked(self, **kw):
        return {self.cust_field: "{{" + self.cust_field + "}}", **kw}

    def outcomes(self, stage):
        return [(o["outcome"], o.get("reason")) for o in self.s["closing"]["outcomes"] if stage in (o.get("stages") or [])]

    def close_tool(self, stage):
        combos = self.outcomes(stage)
        text = "; ".join(o + (" / " + r if r else " (no reason)") for o, r in combos)
        spec = dict(self.close_spec)
        fields = {}
        for f, sch in spec["fields"].items():
            if f == self.cust_field:
                continue
            if f == "outcome" or "outcome" in f:
                fields[f] = "Outcome of the call. Allowed here: " + text
            elif "reason" in f:
                fields[f] = "Reason that goes with the outcome. Allowed here: " + text + ". Leave it empty when the outcome has no reason."
        t = T.tool(spec, baked=self.baked(), model_fields=fields,
                   description=("Ends the call record. CALL it exactly once, as the very last action, in the same message in which "
                                "you thank the customer and say goodbye. Allowed outcome / reason combinations here: " + text + "."),
                   hook=hook("d['what_to_do_next'] = 'The call is closed. Say nothing more.'"), retries=1)
        # restrict the enums to what this stage allows
        body = t["function"]["parameters"]["properties"]["body"]["properties"]
        for f, leaf in body.items():
            if "enum" in leaf and ("outcome" in f):
                leaf["enum"] = sorted({o for o, _ in combos})
            elif "enum" in leaf and ("reason" in f):
                leaf["enum"] = sorted({r for _, r in combos if r}) or leaf["enum"]
        t["pre_process_code"] = ("try:\n    b = config.get('body')\n    if isinstance(b, dict):\n"
                                 "        r = b.get('reason')\n        if r in (None, '', 'null', 'none', 'None') or str(r).startswith('{'):\n"
                                 "            b.pop('reason', None)\nexcept Exception:\n    pass\n")
        return t

    def closer(self, name, line, outcome, reason):
        body = {"outcome": outcome}
        if reason:
            body["reason"] = reason
        spec = self.close_spec
        t = T.tool(spec, baked=self.baked(**body), hook="", retries=1)
        return {"name": name, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "end",
                "on_enter": {"type": "tts", "text": line}, "disable_inactivity_monitor": True,
                "pre_actions": [t], "transitions": [always("end")]}

    # ------------------------------------------------------------------ tools used in conversation nodes
    def transfer_tool(self):
        sp = self.api.get("transfer_to_agent")
        if not sp:
            return None
        return T.tool(sp, baked=self.baked(), model_fields={"queue": self.t["tool_desc"].get("transfer_queue", "")},
                      description=self.t["tool_desc"]["transfer_to_agent"],
                      next_by_case={"ok": self.t["next"]["transfer_ok"], "fail": self.t["next"]["transfer_fail"],
                                    "error": self.t["next"]["transfer_fail"]})

    def callback_tool(self):
        tp = self.s.get("third_party") or {}
        sp = self.api.get(tp.get("tool") or "schedule_callback")
        if not sp:
            return None
        return T.tool(sp, baked=self.baked(), model_fields={f: self.t["tool_desc"].get("callback_" + f, f) for f in sp["fields"] if f != self.cust_field},
                      description=self.t["tool_desc"]["schedule_callback"],
                      next_by_case={"ok": self.t["next"]["callback_ok"], "fail": self.t["next"]["callback_fail"], "error": self.t["next"]["callback_fail"]})

    def verify_tool(self, q, nxt_msg):
        sp = self.api[self.s["identity"]["tool"]]
        fields = {f: s for f, s in sp["fields"].items() if f != self.cust_field}
        field_key = next((f for f, s in fields.items() if "enum" in s and q["field"] in s["enum"]), None)
        model = {f: (q.get("tool_format") or "") + ". " + (q.get("parse_rule") or "") for f in fields if f != field_key}
        baked = self.baked(**({field_key: q["field"]} if field_key else {}))
        hints = self.s["identity"].get("hints") or []
        hint_lines = "".join(f"if str(d.get('hint') or '') == {pylit(h['hint'])}:\n    d['what_to_do_next'] = {pylit(self.t['next']['hint_' + h['hint']])}\nel"
                             for h in hints if 'hint_' + h['hint'] in self.t['next'])
        body = (hint_lines +
                "if str(d.get('all_verified')).lower() == 'true':\n"
                f"    d['what_to_do_next'] = {pylit(self.t['next']['verify_all'])}\n"
                "elif str(d.get('verified')).lower() == 'true':\n"
                f"    d['what_to_do_next'] = {pylit(nxt_msg)}\n"
                "elif str(d.get('wrong_answers_left')) == '0':\n"
                f"    d['what_to_do_next'] = {pylit(self.t['next']['verify_locked'])}\n"
                "elif str(d.get('ok')).lower() == 'false' and 'verified' not in d:\n"
                f"    d['what_to_do_next'] = {pylit(self.t['next']['verify_unreadable'])}\n"
                "else:\n"
                f"    d['what_to_do_next'] = {pylit(self.t['next']['verify_wrong'])}\n")
        return T.tool(sp, baked=baked, model_fields=model, description=self.t["tool_desc"]["verify"], hook=hook(body), retries=1)

    def promise_tool(self):
        c = self.s["confirmation"]
        sp = self.api[c["tool"]]
        model = {f: self.t["tool_desc"].get("promise_" + f, f) for f in sp["fields"] if f != self.cust_field}
        pre = ("try:\n    b = config.get('body')\n    if isinstance(b, dict):\n"
               "        for k, v in list(b.items()):\n"
               "            if isinstance(v, str) and k in ('amount',):\n                b[k] = float(v.replace(',', '').strip())\n"
               "            elif isinstance(v, str) and k in ('date',):\n                b[k] = v.strip()[:10]\nexcept Exception:\n    pass\n")
        body = ("if str(d.get('ok')).lower() == 'true':\n"
                "    n = int(float(vars.get('promises_recorded') or 0)) + 1\n"
                "    vars['promises_recorded'] = n\n"
                f"    d['what_to_do_next'] = {pylit(self.t['next']['promise_ok'])}\n"
                "else:\n"
                "    d['ok'] = False\n"
                "    n = int(float(vars.get('promise_failures') or 0)) + 1\n"
                "    vars['promise_failures'] = n\n"
                f"    d['what_to_do_next'] = {pylit(self.t['next']['promise_fail_first'])} if n <= {int((c.get('on_failure') or {}).get('retries', 1))} else {pylit(self.t['next']['promise_fail_final'])}\n")
        # maxRetries does not repeat a call that failed with HTTP 500 (seen in a practice run): the retry
        # chain built in build() repeats it instead.
        t = T.tool(sp, baked=self.baked(), model_fields=model, description=self.t["tool_desc"]["create_promise"], hook=hook(body), retries=0)
        t["pre_process_code"] = pre
        return t

    def note_tool(self):
        mp = self.s.get("multi_product")
        if not mp or not mp.get("note_tool") or mp["note_tool"] not in self.api:
            return None
        sp = self.api[mp["note_tool"]]
        model = {f: self.t["tool_desc"].get("note_" + f, f) for f in sp["fields"] if f != self.cust_field}
        return T.tool(sp, baked=self.baked(), model_fields=model, description=self.t["tool_desc"]["add_note"],
                      next_by_case={"ok": self.t["next"]["note_ok"], "fail": self.t["next"]["note_fail"], "error": self.t["next"]["note_fail"]})

    def retry_node(self, name, nxt, tool_name, k):
        sp = self.api[tool_name]
        baked = self.baked(**{f: "{{" + tool_name + "_args." + f + "}}" for f in sp["fields"] if f != self.cust_field})
        t = T.tool(sp, baked=baked, hook="", retries=0)
        t["pre_process_code"] = RETRY_PRE.replace("TOOL", tool_name)
        return {"name": name, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": nxt,
                "on_enter": {"type": "tts", "text": self.t["lines"]["retry_announce"]}, "disable_inactivity_monitor": True,
                "pre_actions": [t], "response_mapping": [{"path": "data.ok", "variable": f"retry_ok_{k}"}],
                "transitions": [always(nxt)]}

    # ------------------------------------------------------------------ build
    def build(self):
        s, t = self.s, self.t
        ct = s["closing"]["tool"]
        closed = {"target_node": "end", "description": "the call record was closed", "condition": {"logic": "or", "conditions": [
            {"variable": ct + "_success_count", "operator": "greater_than", "operand": 0},
            {"variable": ct + "_failure_count", "operator": "greater_than", "operand": 1}]}}
        nodes = {}
        nodes["setup"] = code_node("setup", calendar_code.setup_code(s), "opening")

        opening_tools = [x for x in (self.callback_tool(), self.transfer_tool(), self.close_tool("before_confirmation")) if x]
        qs = s["identity"]["questions"]
        first_id = "id_" + qs[0]["field"]
        nodes["opening"] = {**CONV, "name": "opening",
            "on_enter": {"type": "tts", "text": t["lines"]["opening"]}, "on_enter_first_entry_only": True,
            "task_message": t["tasks"]["opening"], "tools": opening_tools,
            "extraction_schema": [{"name": "person_check", "type": "string", "enum_values": ["confirmed", "other"],
                                   "description": t["extraction"]["person_check"], "min_confidence": 0.7}],
            "transitions": [closed, {"target_node": first_id, "description": "the customer confirmed who they are",
                                     "condition": or_eq("person_check", "confirmed")}]}

        locked = {"target_node": "identity_failed", "description": "no wrong answers left",
                  "condition": or_eq(self.vt + "_response.data." + self.r_left, "0")}
        for i, q in enumerate(qs):
            last = i == len(qs) - 1
            nxt = "get_account" if last else "id_" + qs[i + 1]["field"]
            nxt_msg = t["next"]["verify_all"] if last else t["next"]["verify_next_" + qs[i + 1]["field"]]
            ok_var = self.vt + "_response.data." + (self.r_all if last else self.r_one)
            nodes["id_" + q["field"]] = {**CONV, "name": "id_" + q["field"],
                "on_enter": {"type": "tts", "text": t["lines"]["ask_" + q["field"]]}, "on_enter_first_entry_only": True,
                "task_message": t["tasks"]["id_" + q["field"]],
                "tools": [x for x in (self.verify_tool(q, nxt_msg), self.transfer_tool(), self.close_tool("during_identity")) if x],
                "transitions": [closed, locked, {"target_node": nxt, "description": "answer verified", "condition": truthy(ok_var)}]}
        f = s["identity"]["on_failed"]
        nodes["identity_failed"] = self.closer("identity_failed", t["lines"]["identity_failed"], f["outcome"], f.get("reason"))

        acc = self.api[s["account"]["tool"]]
        list_field = next((k for k, v in acc["response"].items() if v.get("type") == "array"), "products")
        nodes["get_account"] = {"name": "get_account", "node_type": "api_request", "passthrough": True,
            "passthrough_default_transition": "prepare", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
            "pre_actions": [T.tool(acc, baked=self.baked(), hook="", retries=2)],
            "response_mapping": [{"path": "data." + list_field, "variable": "products"}],
            "transitions": [always("prepare")]}
        nodes["prepare"] = code_node("prepare", t["prepare_code"], "collection", [
            {"target_node": "account_error", "description": "no account data", "condition": {"logic": "or", "conditions": [
                {"variable": "account_state", "operator": "not_equals", "operand": "ok"}]}}])
        sef = s["confirmation"]["on_failure"]
        nodes["account_error"] = self.closer("account_error", t["lines"]["account_error"], sef.get("outcome", "no_promise"), sef.get("reason"))

        coll_tools = [x for x in (self.promise_tool(), self.note_tool(), self.transfer_tool(), self.close_tool("after_verification")) if x]
        nodes["collection"] = {**CONV, "name": "collection", "max_node_turns": 80,
            "on_enter": {"type": "tts", "text": t["lines"]["statement"]}, "on_enter_first_entry_only": True,
            "task_message": t["tasks"]["collection"], "tools": coll_tools, "transitions": [closed]}
        # A failed promise call reaches the model as a bare error and the model tends to give up. The engine
        # repeats it instead: one retry node per possible product, chained by the failure counter.
        retries = int((s["confirmation"].get("on_failure") or {}).get("retries") or 0)
        if retries:
            pt = s["confirmation"]["tool"]
            n_max = int((s.get("multi_product") or {}).get("max_products") or 1)
            multi = bool(s.get("multi_product"))
            of = s["confirmation"].get("on_failure") or {}
            prevs = ["collection"]
            for k in range(1, n_max + 1):
                rn, ck, ok_n, bad_n = f"promise_retry_{k}", f"retry_check_{k}", f"retry_recorded_{k}", f"retry_failed_{k}"
                fail = {"target_node": rn, "description": "a promise call failed", "condition": gt(pt + "_failure_count", k - 1)}
                for p in prevs:
                    nodes[p]["transitions"].append(fail)
                nodes[rn] = self.retry_node(rn, ck, pt, k)
                nodes[ck] = code_node(ck, RETRY_CHECK.replace("K", str(k)), bad_n, [
                    {"target_node": ok_n, "description": "the retry recorded the promise", "condition": or_eq(f"retry_state_{k}", "ok")}])
                cont = {**CONV, "max_node_turns": 80, "on_enter_first_entry_only": True,
                        "task_message": t["tasks"]["collection"], "tools": coll_tools, "transitions": [closed]}
                nodes[ok_n] = {**cont, "name": ok_n, "on_enter": {"type": "tts", "text": t["lines"]["retry_recorded"]},
                               "transitions": [closed]}
                if multi:
                    nodes[bad_n] = {**cont, "name": bad_n, "on_enter": {"type": "tts", "text": t["lines"]["retry_failed_multi"]},
                                    "transitions": [closed]}
                    prevs = [ok_n, bad_n]
                else:
                    nodes[bad_n] = self.closer(bad_n, t["lines"]["retry_failed_single"], of.get("outcome", "no_promise"), of.get("reason"))
                    prevs = [ok_n]
        nodes["end"] = {"name": "end", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
            "transitions": [], "terminal": {"type": "function", "function": {"name": "call_closed", "handler": "__function__:end_call",
                "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}}}

        tv = {v["name"]: "" for v in s.get("call_variables", [])}
        tv.update(t.get("test_variables") or {})
        return {"initial_node": "setup", "system_prompt": t["system_prompt"], "test_variables": tv,
                "global_nodes": {}, "nodes": nodes}


def compile_workflow(spec, api, texts):
    return Compiler(spec, api, texts).build()
