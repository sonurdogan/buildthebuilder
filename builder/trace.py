"""Builder trace: one JSONL file per run. Holds what the rules ask for: start and end time,
every model call (model and purpose), what was created on Freya, and every self-test with its result.
Secrets are scrubbed before anything is written."""
import json, os, re, threading, time, pathlib

_LOCK = threading.Lock()

from .env import ROOT

_SECRET = re.compile(r"(fak_[A-Za-z0-9_\-]+|sk-ant-[A-Za-z0-9_\-]+)")


def _scrub(obj):
    s = json.dumps(obj, default=str, ensure_ascii=False)
    for name in ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY", "ANTHROPIC_API_KEY"):
        v = os.environ.get(name)
        if v and len(v) > 6:
            s = s.replace(v, f"<{name}>")
    return json.loads(_SECRET.sub("<secret>", s))


class Trace:
    def __init__(self, label):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.path = ROOT / "traces" / f"{label}-{stamp}.jsonl"
        self.path.parent.mkdir(exist_ok=True)
        self.t0 = time.time()
        self.model_calls = 0
        self.event("start", label=label, at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    def event(self, kind, **data):
        rec = {"t": round(time.time() - getattr(self, "t0", time.time()), 2), "event": kind, **data}
        with _LOCK, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_scrub(rec), ensure_ascii=False) + "\n")

    def model_call(self, model, purpose, seconds, input_tokens=None, output_tokens=None, **extra):
        with _LOCK:
            self.model_calls += 1
        self.event("model_call", model=model, purpose=purpose, seconds=round(seconds, 2),
                   input_tokens=input_tokens, output_tokens=output_tokens, **extra)

    def end(self, **summary):
        self.event("end", at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   total_seconds=round(time.time() - self.t0, 1), model_calls=self.model_calls, **summary)


_current = None


def current():
    return _current


def start(label):
    global _current
    _current = Trace(label)
    return _current
