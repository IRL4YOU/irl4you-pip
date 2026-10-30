
import gzip
import hashlib
import os
import re
import threading
from srv.util import BASE_DIR, WEB_DIR, read

ASSET_RE = re.compile(r"^/(?:js/[a-z0-9_-]+\.js|app\.css|chat\.js|chat\.css)$")
TYPES = {".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8"}
MIN_GZ = 600


class WebFiles:

    def __init__(self, root=None, version=None):
        self.root = root or WEB_DIR
        self.version = (version if version is not None else (read(os.path.join(BASE_DIR, "VERSION"), "") or "")).strip()
        self.cache = {}
        self.lock = threading.Lock()

    def entry(self, name, page=False):
        path = os.path.join(self.root, name)
        try:
            st = os.stat(path)
        except OSError:
            return None
        sig = (st.st_mtime_ns, st.st_size)
        hit = self.cache.get((name, page))
        if hit is not None and hit["sig"] == sig:
            return hit
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return None
        if page:
            body = body.replace(b"__V__", self.version.encode("ascii", "replace"))
        gz = gzip.compress(body, 6, mtime=0) if len(body) >= MIN_GZ else None
        tag = hashlib.sha1(body).hexdigest()[:20]
        e = {"sig": sig, "body": body, "gz": gz if gz is not None and len(gz) < len(body) else None, "etag": '"%s"' % tag, "etag_gz": '"%s-gz"' % tag,
             "ctype": TYPES.get(os.path.splitext(name)[1], "application/octet-stream")}
        with self.lock:
            self.cache[(name, page)] = e
        return e
