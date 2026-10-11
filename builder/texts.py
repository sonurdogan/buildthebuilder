"""Step 4a: the builder's model words everything the phone agent says or reads: system prompt, fixed
lines, task messages, tool descriptions, tool-result instructions, and the derived-date function.
The structure they plug into is fixed by builder/compile.py. Output is validated and repaired once."""
import json, re

from . import calendar_code, llm, trace

LESSONS = """Authoring rules for the phone agent (learned on the platform; follow all of them):
- The phone agent runs on a small model. Write short, concrete, numbered instructions. No vague wording.
- Fixed lines (lines.*) are spoken verbatim by the engine. Each task message starts with
  'You have just said: "<the fixed line>"' so the model knows what was said.
- A task message covers only its own step. Never mention nodes, steps or routing.
- Tools: when a rule says to call a tool, the model must call it; saying it is not enough. One tool per message.
  After every tool result: read what_to_do_next, follow it, and always say something. Never end a turn with nothing.
- Honesty: never say something is recorded, booked or arranged unless the tool returned ok true.
- Every call ends with the closing tool. In that same final message the agent first says the brief's goodbye.
  Pick the outcome and reason strictly from the allowed combinations of that step.
- Dates: the model never computes dates. It uses the calendar and the facts blocks given in the prompt
  ({{calendar_text}}, {{products_facts}}, {{callback_days_text}}). It converts what the customer says
  (words, parts, corrections) into exact values: dates YYYY-MM-DD, amounts as numbers.
- Customers talk like people: they give a number in two parts, correct themselves, add small talk, ask a
  question back, or say amounts and dates in words. The agent waits for the complete value, uses the corrected
  value, answers a question briefly, then continues.
- Identity: never judge an answer yourself; call the verify tool as soon as a complete answer is given.
  A complete answer that comes with a question, a complaint or small talk ("21 July 1990. How much do I owe?")
  is still an answer: call the verify tool first, then deal with the rest. Only a turn with no answer at all
  can count as declining. A wrong answer is never a reason to explain why the questions are asked.
  Never reveal or hint at the correct answer.
- Before verification, never say anything the brief forbids (words, reasons for the call, products, amounts).
- Negotiation: keep track of every offer the customer refused and never repeat it. Count persuasion attempts
  exactly as the brief defines them. A proposal by the customer is handled, not counted as a refusal, when the
  brief says so. Put special situations (paid, dispute, hardship, transfers ...) first, with the brief's precedence.
- A failed tool call can reach the model as a bare error with no what_to_do_next. The task message itself must
  say what to do when a tool fails (the brief's retry and give-up rule), and must never let the agent claim success.
  The platform already repeats a failed promise call as often as the brief allows, so the agent does not call it again.
- When the customer names a day that is not a business day, the calendar line of that day gives the next business
  day. The task message must tell the agent to look the day up in the calendar and follow the brief's rule for it.
- derived_code returns exactly the dates named in spec.negotiation.derived_dates, nothing else.
- Required sentences from the brief are given verbatim and the prompt says exactly when to say them.
- English unless the brief says otherwise. One or two short sentences per turn. No lists or markdown in speech."""

VARS = ["today_text", "today_iso", "now_time", "calendar_text", "callback_days_text", "callback_days_iso",
        "products_facts", "statement_text", "product_count", "product_ids"]


def contracts(spec, api):
    q = spec["identity"]["questions"]
    stage = lambda st: [(o["outcome"], o.get("reason")) for o in spec["closing"]["outcomes"] if st in (o.get("stages") or [])]
    mp = bool(spec.get("multi_product"))
    c = {
        "opening": {"entered": "start of the call, after the fixed opening line (lines.opening)",
                    "job": "find out who is on the line; handle someone else answering, wrong number, deceased, unclear, questions before confirmation",
                    "tools": ["schedule_callback", "transfer_to_agent", "close_call"], "close_allowed": stage("before_confirmation"),
                    "leaves_when": "the person confirms being the customer (extraction person_check=confirmed); the engine then asks the first security question"},
    }
    for i, x in enumerate(q):
        c["id_" + x["field"]] = {"entered": f"after the fixed line lines.ask_{x['field']}",
            "job": f"security question {i + 1} of {len(q)}: {x['field']}; call verify tool with the answer in the tool format",
            "tools": ["verify_identity (field is fixed to " + x["field"] + ")", "transfer_to_agent", "close_call"],
            "close_allowed": stage("during_identity"),
            "leaves_when": "the verify tool says verified (or all verified); too many wrong answers leads to a fixed closing line"}
    c["collection"] = {"entered": "after verification; the engine fetched the account and spoke lines.statement",
        "job": "the whole negotiation for " + ("every product" if mp else "the product") + ": promises, read-back, recording, special situations, notes, transfers, closing",
        "tools": ["create_promise", "add_note"] if mp else ["create_promise"], "close_allowed": stage("after_verification"),
        "variables": "{{products_facts}} holds per product: id, name, full amount, floor, band, deadline, valid dates, attempts, derived dates"}
    c["collection"]["tools"] += ["transfer_to_agent", "close_call"]
    return c


def required_keys(spec):
    q = [x["field"] for x in spec["identity"]["questions"]]
    hints = [h["hint"] for h in (spec["identity"].get("hints") or [])]
    return {
        "lines": ["opening"] + ["ask_" + f for f in q] + ["identity_failed", "account_error", "statement", "product_sentence", "retry_announce", "retry_recorded", "retry_failed_single", "retry_failed_multi"],
        "tasks": ["opening"] + ["id_" + f for f in q] + ["collection"],
        "extraction": ["person_check"],
        "tool_desc": ["transfer_to_agent", "transfer_queue", "schedule_callback", "verify", "create_promise", "add_note"],
        "next": ["transfer_ok", "transfer_fail", "callback_ok", "callback_fail", "verify_all", "verify_locked",
                 "verify_unreadable", "verify_wrong", "promise_ok", "promise_fail_first", "promise_fail_final", "note_ok", "note_fail"]
                + ["verify_next_" + f for f in q[1:]] + ["hint_" + h for h in hints],
    }


def problems(spec, texts):
    out = []
    req = required_keys(spec)
    for sec, keys in req.items():
        for k in keys:
            if not str((texts.get(sec) or {}).get(k) or "").strip():
                out.append(f"missing {sec}.{k}")
    if not str(texts.get("system_prompt") or "").strip():
        out.append("missing system_prompt")
    call_vars = {v["name"] for v in spec.get("call_variables", [])}
    known = set(VARS) | call_vars
    blob = json.dumps(texts, ensure_ascii=False)
    for v in sorted(set(re.findall(r"\{\{\s*([\w.\[\]]+)\s*\}\}", blob))):
        if v not in known:
            out.append(f"unknown placeholder {{{{{v}}}}}; allowed: {sorted(known)}")
    if "{{statement_text}}" not in str((texts.get("lines") or {}).get("statement", "")):
        out.append("lines.statement must contain {{statement_text}}")
    forbidden = [w.lower() for w in spec.get("forbidden_before_verification") or [] if len(w.split()) <= 2]
    for k in ["opening"] + ["ask_" + x["field"] for x in spec["identity"]["questions"]] + ["identity_failed"]:
        line = str((texts.get("lines") or {}).get(k, "")).lower()
        for w in forbidden:
            if re.search(r"\b" + re.escape(w) + r"\b", line):
                out.append(f"lines.{k} contains '{w}', which is forbidden before verification")
    try:
        p = calendar_code.check_derived(spec, texts.get("derived_code") or "")
        if p:
            out.append("derived_code fails: " + "; ".join(p))
    except Exception as e:
        out.append(f"derived_code fails: {e}")
    return out


def write(spec, rules, api_text, api):
    req = required_keys(spec)
    prompt = f"""<spec>
{json.dumps(spec, indent=1, ensure_ascii=False)}
</spec>

<rules>
{json.dumps(rules, indent=1, ensure_ascii=False)}
</rules>

<tool_api>
{api_text}
</tool_api>

<workflow_steps>
{json.dumps(contracts(spec, api), indent=1, ensure_ascii=False)}
</workflow_steps>

<prompt_variables>
These variables are filled by code and may be used as {{{{name}}}} in any text: {", ".join(VARS)},
and the call variables: {", ".join(v["name"] for v in spec.get("call_variables", []))}.
</prompt_variables>

{LESSONS}

Write every text of the phone agent for this bank. The workflow steps above are fixed; you only write
the words. Every rule in <rules> must be carried by the text of the step where it applies (or the system
prompt when it applies everywhere). Copy required sentences verbatim.

The full set of texts (several writers work in parallel; each writes only the keys it is given):
- "system_prompt": identity, language, tone, and the rules that hold in every step (questions about being a
  robot, scam questions, unknown information, never offering terms outside the brief, tool and honesty rules).
- "lines": fixed spoken lines. Keys: {req["lines"]}.
  lines.opening: the brief's first sentence plus the confirmation question.
  lines.ask_<field>: the security question (first one may say there are security questions).
  lines.identity_failed / lines.account_error: what to say before the engine closes the call with the
  failed-identity outcome / the service-error outcome, ending with the goodbye.
  lines.statement: spoken right after verification; must contain {{{{statement_text}}}} (one sentence per product,
  built by code) and end with the brief's first question.
  lines.retry_announce: spoken when a promise call failed and the engine tries it once more (short, honest:
  a technical problem, trying again; it does not say anything is recorded).
  lines.retry_recorded: spoken when that retry recorded the promise: that it is recorded now, plus the brief's
  required sentence after a recorded promise, then a short question whether there is anything else (multi-product
  briefs: "let us continue" instead). The negotiation step then goes on as usual.
  lines.retry_failed_single: spoken when the retry failed too and the customer has one product: that it could not
  be recorded, then the brief's goodbye. The engine then closes with the brief's service-error outcome.
  lines.retry_failed_multi: spoken when the retry failed too and there can be more products: that it could not be
  recorded for that product, then a short bridge to continue. The negotiation step then handles notes and the rest.
  The task message of the negotiation step must say: after one of these lines, never call the promise tool
  again for that product.
  lines.product_sentence: template for one product's sentence, using [phrase], [past_due], [days_late] and any
  other product field in square brackets, e.g. [card_last4]. No curly braces in it.
- "tasks": task message per conversational step. Keys: {req["tasks"]}.
- "extraction": {{"person_check": description for an extractor that returns 'confirmed' only when the person
  clearly confirmed being the customer, by the brief's definition, and 'other' otherwise}}.
- "tool_desc": tool descriptions that say exactly WHEN to call. Keys: {req["tool_desc"]}, plus optional
  "promise_<field>", "note_<field>", "callback_<field>" descriptions for the fields the model fills.
- "next": the instruction added to a tool result (what_to_do_next). Keys: {req["next"]}.
  verify_next_<field>: after a correct answer, ask the next question. hint_<hint>: what to do for that hint.
  promise_ok must contain the brief's required sentence after a recorded promise, if any.
  promise_fail_first: retry case; promise_fail_final: give-up case with the brief's outcome.
- "derived_code": Python source for the derived dates of the spec (spec.negotiation.derived_dates).
{calendar_code.DERIVED_HELP}
- "test_variables": example values for the call variables.

Answer with the JSON object only."""
    groups = groups_for(spec)
    tr = trace.current()

    def job(name, keys):
        return lambda: llm.ask_json(
            prompt + f"\n\nYou write ONLY this part: {json.dumps(keys)}. Return a JSON object with exactly that "
            "structure (top-level keys, and for lines/tasks/tool_desc/next/extraction only the listed sub-keys).",
            f"write phone agent texts, part: {name}")

    parts = llm.parallel({n: job(n, k) for n, k in groups.items()})
    texts = {}
    for part in parts.values():
        deep_merge(texts, part)
    errs = problems(spec, texts)
    if errs:
        if tr:
            tr.event("texts_problems", problems=errs)
        fix = llm.ask_json(prompt + "\n\n<current_texts>\n" + json.dumps(texts, ensure_ascii=False) +
                           "\n</current_texts>\n\nFix these problems. Return ONLY the keys you change, in the same "
                           "nested structure:\n- " + "\n- ".join(errs),
                           "fix problems found by the validator in the phone agent texts")
        deep_merge(texts, fix)
        errs = problems(spec, texts)
    if tr:
        tr.event("texts_written", remaining_problems=errs)
    return texts, errs


def deep_merge(a, b):
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            deep_merge(a[k], v)
        else:
            a[k] = v
    return a


def groups_for(spec):
    """Split the texts into parts that can be written at the same time."""
    r = required_keys(spec)
    q = [x["field"] for x in spec["identity"]["questions"]]
    pick = lambda sec, pred: [k for k in r[sec] if pred(k)]
    first = lambda k: k in ("opening", "identity_failed") or k.startswith(("ask_", "id_", "verify", "hint_", "callback"))
    return {
        "opening_and_identity": {"system_prompt": True, "lines": pick("lines", first), "tasks": pick("tasks", first),
                                 "extraction": r["extraction"], "tool_desc": ["verify", "schedule_callback", "callback_<field> (optional)"],
                                 "next": pick("next", first)},
        "negotiation_task": {"tasks": ["collection"], "lines": ["statement", "product_sentence"]},
        "negotiation_tools": {"lines": [k for k in r["lines"] if not first(k) and k not in ("statement", "product_sentence")],
                              "tool_desc": ["create_promise", "add_note", "transfer_to_agent", "transfer_queue",
                                            "promise_<field> (optional)", "note_<field> (optional)"],
                              "next": [k for k in r["next"] if not first(k)]},
        "code": {"derived_code": True, "test_variables": True},
    }
