"""Append-only JSON-lines log of model calls and requests: logs/requests.jsonl."""
import json
import threading
import time
from pathlib import Path

PATH = Path("logs/requests.jsonl")
_lock = threading.Lock()


def log(**fields):
    line = json.dumps({"ts": round(time.time(), 3)} | fields, ensure_ascii=False)
    try:
        with _lock:
            PATH.parent.mkdir(exist_ok=True)
            with open(PATH, "a") as f:
                f.write(line + "\n")
    except OSError:  # a full disk must not fail the request
        pass
