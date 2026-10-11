# Builder design

Input: one or more brief files (clean spec or raw bank material) and the tool API file.
Output: one published Freya workflow, its export, and a trace. No manual step. Budget: 30 minutes.

## Principle

The model reads; code enforces. Everything that can be computed (dates, deadlines, amount limits,
attempt counters, which tool is allowed when) is computed in code nodes, hooks and variable
transitions. The phone agent's model only talks, and only sees the facts it needs in each step.

## Pipeline

```
briefs + openapi
   |
1. INGEST     read every file, keep source order (a later memo can override an earlier policy)
2. EXTRACT    LLM -> spec.json  (fixed schema, every field cites the rule text it came from)
              + rules.json      (every atomic rule of the brief, numbered)
              + unmapped list   (rules the schema cannot express)
3. RECONCILE  LLM second pass: conflicts, overrides, missing values; checks spec against rules.json
4. COMPILE    Python, deterministic: spec.json -> workflow JSON
              - code node at start: business calendar, deadlines per band, callback window, end of week
              - identity node: tool-only exit on verify result, wrong-answer counter from the API
              - account node (api_request), debt statement, negotiation, confirm + record, closings
              - global nodes for intents that can come anytime (human, scam, robot, not-interested)
              - post-hooks on every tool add a `what_to_do_next` line computed from the result
5. PATCH      LLM edits for rules in the unmapped list (prompt text, extra nodes), JSON-validated
6. CHECK      static lint + Freya dryRun + coverage: every rule id must map to a node, hook or prompt
7. SELF-TEST  LLM writes test customers from the brief (same format as public-test-set.json);
              run them on Freya (simulation or chat turns); read ledger + transcript
8. REPAIR     failures -> targeted spec/patch changes -> recompile -> retest, until time budget ends
9. EXPORT     save_workflow (slot per brief, updated in place), export_workflow, close trace
```

## What is brief-specific (comes from the spec, never from code)

Call variables, opening sentence, forbidden words before verification, identity questions and their
order/format, retry limits, third-party and callback rules, business days and holidays, bands and
deadlines, floors per product type, product naming, persuasion ladder, intents and their actions,
mandatory sentences, outcome/reason table, note rules, multi-product rules, tool fields and enums.

## What is generic (code, reusable for any brief of this kind)

Workflow skeleton patterns, calendar arithmetic, date/amount validators, tool wrappers built from
the OpenAPI file, hook templates, platform client, test runner, trace.

## Open questions (answered by scripts/probe.py)

- Can the mock bank create practice customers from cases we write? Decides how self-test works.
- Chat-turn API speed and behaviour, for fast checks without the simulation queue.
