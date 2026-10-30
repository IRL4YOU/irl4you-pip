
import json
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


_JSON = {}
JSON_MAX_AGE = 5.0


def json_file(path):
    try:
        st = os.stat(path)
    except OSError:
        _JSON.pop(path, None)
        return None
    sig = (st.st_mtime_ns, st.st_size, st.st_ino)
    now = time.monotonic()
    hit = _JSON.get(path)
    if hit is not None and hit[0] == sig and now - hit[1] < JSON_MAX_AGE:
        return hit[2]
    try:
        with open(path) as f:
            val = json.load(f)
    except (OSError, ValueError):
        val = None
    _JSON[path] = (sig, now, val)
    return val

