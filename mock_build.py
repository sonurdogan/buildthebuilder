"""Write, deploy and check the bank's mock services from a case folder. Run it before build.py --pack.

    python mock_build.py <case_folder> --name WORKER_NAME [--repairs 2]

Steps: read the folder -> derive the bank operations -> write the Worker -> deploy with wrangler ->
call it with the test requests derived from the bank's test data -> repair and redeploy on failures.
Trace: traces/mock-<name>-<time>.jsonl. The Worker serves the operations it implements at
/__builder/api.json; build.py reads them from there."""
import argparse, json, time

from builder import env  # noqa: F401
from builder import mock, pack, trace

ROOT = env.ROOT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--name", required=True)
    ap.add_argument("--repairs", type=int, default=2)
    a = ap.parse_args()
    tr = trace.start("mock-" + a.name)
    t0 = time.time()
    work = ROOT / "work" / ("mock-" + a.name)
    work.mkdir(parents=True, exist_ok=True)
    material, files = pack.read_folder(a.folder)
    tr.event("inputs", folder=a.folder, files=files, chars=len(material))
    log(t0, f"read {len(files)} files, {len(material)} chars")
    data = pack.data_files(a.folder)
    tr.event("data_files", files={k: len(json.dumps(v)) for k, v in data.items()})
    api = pack.derive_api(material)
    (work / "api.json").write_text(json.dumps(api, indent=1, ensure_ascii=False), encoding="utf-8")
    log(t0, f"{len(api['operations'])} operations: " + ", ".join(f"{o['method']} {o['path']}" for o in api["operations"]))
    code, tests = mock.write_worker(material, api, list(data))
    fails, url = None, None
    for attempt in range(a.repairs + 1):
        (work / "tests.json").write_text(json.dumps(tests, indent=1, ensure_ascii=False), encoding="utf-8")
        url = mock.deploy(a.name, mock.source(code, api, data), work / "worker")
        log(t0, f"deployed {url}")
        time.sleep(3)
        fails = mock.check(url, tests)
        log(t0, f"self-test: {len(tests) - len(fails)}/{len(tests)} passed")
        if not fails or attempt == a.repairs:
            break
        for f in fails[:8]:
            print("  FAIL", f.get("name"), f.get("problems") or f.get("error"))
        code, tests = mock.write_worker(material, api, list(data), failures=fails, previous=code)
    (work / "fails.json").write_text(json.dumps(fails, indent=1, ensure_ascii=False), encoding="utf-8")
    tr.end(mock_url=url, tests=len(tests), failed=len(fails or []))
    log(t0, f"done. mock {url}, trace {tr.path}")


def log(t0, msg):
    print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)


if __name__ == "__main__":
    main()
