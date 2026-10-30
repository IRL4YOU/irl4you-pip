
import os
import time


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "web")


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


_TTL = {}


def ttl_cached(key, ttl, fn):
    now = time.monotonic()
    hit = _TTL.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _TTL[key] = (now, val)
    return val


def ttl_cached_drop(key):
    _TTL.pop(key, None)


NOT_HANDLED = object()
