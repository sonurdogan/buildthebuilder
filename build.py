"""Build a Freya phone agent from a bank's brief. One command, no manual step.

    python build.py <brief file or folder> [more brief files] --api tools/openapi.json [--slot NAME] [--cases A]

Steps: extract -> reconcile -> write texts -> compile -> lint + dry run -> save and publish -> optional
self-test on practice customers -> export. Every model call, everything created on Freya and every
self-test goes to traces/<slot>-<time>.jsonl. Output: out/<slot>.workflow.json (the export to submit).
"""
import argparse, json, os, pathlib, subprocess, sys, time

from builder import env  # noqa: F401  (loads .env before freya reads the key)
from builder import calendar_code, compile as C, extract, lint, texts as X, tools, trace
import freya

ROOT = env.ROOT
BUDGET_S = 30 * 60


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("briefs", nargs="+")
    ap.add_argument("--api", default=str(ROOT / "tools" / "openapi.json"))
    ap.add_argument("--slot")
    ap.add_argument("--cases", help="practice cases to self-test on, e.g. A or B (run_cases --brief)")
    ap.add_argument("--reuse", help="reuse spec/texts from this work folder (development only)")
    ap.add_argument("--new-texts", action="store_true", help="with --reuse: keep the spec, write the texts again")
    ap.add_argument("--pack", action="store_true", help="the input is a case folder of raw bank material")
    ap.add_argument("--mock-url", help="with --pack: the deployed mock (mock_build.py) the agent's tools call")
    a = ap.parse_args()
    if a.pack:
        return build_pack(a)
    slot = a.slot or pathlib.Path(a.briefs[-1]).stem
    work = ROOT / "work" / slot
    work.mkdir(parents=True, exist_ok=True)
    tr = trace.start(slot)
    t0 = time.time()
    tr.event("inputs", briefs=a.briefs, api=a.api, slot=slot)

    brief_text, api_text = extract.read_inputs(a.briefs, a.api)
    api = tools.load_api(a.api)
    reuse = pathlib.Path(a.reuse) if a.reuse else None
    if reuse and (reuse / "spec.json").exists():
        spec = json.loads((reuse / "spec.json").read_text(encoding="utf-8"))
        rules = json.loads((reuse / "rules.json").read_text(encoding="utf-8"))
        tr.event("reused_spec", folder=str(reuse))
    else:
        rules, spec = extract.extract(brief_text, api_text, work)
        spec, changes, missing = extract.reconcile(brief_text, rules, spec, work)
    log(t0, f"spec ready: {len(rules)} rules")

    if reuse and (reuse / "texts.json").exists() and not a.new_texts:
        texts = json.loads((reuse / "texts.json").read_text(encoding="utf-8"))
        errs = X.problems(spec, texts)
    else:
        texts, errs = X.write(spec, rules, api_text, api)
    (work / "texts.json").write_text(json.dumps(texts, indent=1, ensure_ascii=False), encoding="utf-8")
    if errs:
        log(t0, "text problems left: " + "; ".join(errs))
    acc = api[spec["account"]["tool"]]
    list_field = next((k for k, v in acc["response"].items() if v.get("type") == "array"), "products")
    texts["prepare_code"] = calendar_code.prepare_code(spec, list_field, texts["lines"]["product_sentence"], texts["derived_code"])
    log(t0, "texts ready")

    wf = C.compile_workflow(spec, api, texts)
    issues = lint.check(wf)
    tr.event("compiled", nodes=list(wf["nodes"]), lint=issues)
    if issues:
        log(t0, "lint: " + "; ".join(issues))
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (work / "workflow.json").write_text(json.dumps(wf, indent=1, ensure_ascii=False), encoding="utf-8")
    freya.validate_workflow(slot, wf)
    saved = freya.save_workflow(slot, wf)
    tr.event("saved_on_freya", **saved, steps=len(wf["nodes"]),
             tools=sum(len(n.get("tools", [])) + len(n.get("pre_actions", [])) for n in wf["nodes"].values()))
    log(t0, f"saved: {saved}")

    if a.cases:
        res = work / f"selftest-{int(time.time())}.json"
        cmd = [sys.executable, str(ROOT / "tools" / "run_cases.py"), saved["agent_id"], "--brief", a.cases, "--out", str(res)]
        # All practice calls in one wave: the run takes as long as the slowest call, not two waves of five.
        envs = dict(os.environ, FREYA_SIM_CONCURRENCY=os.environ.get("FREYA_SIM_CONCURRENCY", "15"))
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=envs)
        print(p.stdout[-6000:], p.stderr[-2000:])
        r = json.loads(res.read_text()) if res.exists() else {}
        tr.event("self_test", cases=a.cases, passed=r.get("passed"), total=r.get("total"),
                 failed=[f["id"] for f in r.get("final", []) if not f["pass"]])

    path = freya.export_workflow(saved["workflow_id"], str(out / f"{slot}.workflow.json"))
    tr.end(workflow_id=saved["workflow_id"], agent_id=saved["agent_id"], export=path)
    log(t0, f"done. export {path}, trace {tr.path}")


def build_pack(a):
    """Case folder of raw material -> phone agent whose tools call the deployed mock."""
    import re
    from builder import agent, mock, pack
    slot = a.slot or pathlib.Path(a.briefs[0]).resolve().name
    work = ROOT / "work" / slot
    work.mkdir(parents=True, exist_ok=True)
    tr = trace.start(slot)
    t0 = time.time()
    material, files = pack.read_folder(a.briefs[0])
    tr.event("inputs", folder=a.briefs[0], files=files, chars=len(material), mock_url=a.mock_url, slot=slot)
    api = mock.fetch_api(a.mock_url)
    log(t0, f"{len(files)} files; mock serves {len(api['operations'])} operations")
    if a.reuse and (pathlib.Path(a.reuse) / "texts.json").exists() and not a.new_texts:
        texts = json.loads((pathlib.Path(a.reuse) / "texts.json").read_text(encoding="utf-8"))
    else:
        texts = agent.write(material, api)
        (work / "texts.draft.json").write_text(json.dumps(texts, indent=1, ensure_ascii=False), encoding="utf-8")
        log(t0, "texts written")
        texts = agent.review(material, api, texts)
    errs = agent.problems(texts, api)
    if errs:
        log(t0, "text problems: " + "; ".join(errs))
        # an unknown {{var}} would be substituted empty: keep the word, drop the braces
        bad = {m.split("{{")[1].split("}}")[0] for m in errs if m.startswith("unknown variable")}
        blob = json.dumps(texts, ensure_ascii=False)
        for v in bad:
            blob = re.sub(r"\{\{\s*" + re.escape(v) + r"\s*(?::json)?\}\}", v, blob)
        texts = json.loads(blob)
    (work / "texts.json").write_text(json.dumps(texts, indent=1, ensure_ascii=False), encoding="utf-8")
    tr.event("texts", problems=errs, rules=len(texts.get("rules") or []))
    log(t0, "texts ready")
    wf = agent.compile_workflow(api, texts, a.mock_url)
    (work / "workflow.json").write_text(json.dumps(wf, indent=1, ensure_ascii=False), encoding="utf-8")
    issues = lint.check(wf)
    tr.event("compiled", nodes=list(wf["nodes"]), lint=issues)
    if issues:
        log(t0, "lint: " + "; ".join(issues))
    freya.validate_workflow(slot, wf)
    saved = freya.save_workflow(slot, wf)
    tr.event("saved_on_freya", **saved, steps=len(wf["nodes"]), tools=len(wf["nodes"]["main"]["tools"]))
    log(t0, f"saved: {saved}")
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    path = freya.export_workflow(saved["workflow_id"], str(out / f"{slot}.workflow.json"))
    tr.end(workflow_id=saved["workflow_id"], agent_id=saved["agent_id"], export=path)
    log(t0, f"done. export {path}, trace {tr.path}")


def log(t0, msg):
    print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)


if __name__ == "__main__":
    main()
