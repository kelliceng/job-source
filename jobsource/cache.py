"""A tiny on-disk cache for LinkedIn responses.

LinkedIn rate-limits hard (HTTP 429), and during development you re-run the
same URLs constantly. Caching is both what makes iteration bearable and what
you'd want in production anyway.
"""
import hashlib
import json
import os
import time
from typing import Optional

CACHE_DIR = os.environ.get("JOBSOURCE_CACHE", ".cache")
TTL = 60 * 60 * 24 * 7   # a week; job boards don't move often


def _path(key: str) -> str:
    h = hashlib.sha256(key.encode()).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"{h}.json")


def get(key: str) -> Optional[dict]:
    p = _path(key)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            rec = json.load(f)
        if time.time() - rec.get("at", 0) > TTL:
            return None
        return rec.get("val")
    except (OSError, json.JSONDecodeError):
        return None


def put(key: str, val: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(_path(key), "w") as f:
            json.dump({"at": time.time(), "val": val}, f)
    except OSError:
        pass
