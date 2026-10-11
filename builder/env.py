"""Load .env into os.environ before freya.py reads it. Import this first."""
import os, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(path=ROOT / ".env"):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().removeprefix("export ").strip(), v.strip().strip('"').strip("'")
        if v and k not in os.environ:
            os.environ[k] = v


load()
