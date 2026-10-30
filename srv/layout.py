
import json
import os
import re
import threading
import urllib.parse


class LayoutStore:
    PROFILES = ("desktop", "mobile")
    DESIGNS = ("", "klar", "kompakt", "kompakt-std", "kontrast", "cockpit", "mischpult")
    ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
    MAX_IDS = 200

    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, "layout.json")
        self.lock = threading.Lock()

    @classmethod
    def clean(cls, d):
        d = d if isinstance(d, dict) else {}
        out = {}
        for k in ("order", "hidden"):
            seen, res = set(), []
            for x in (d.get(k) if isinstance(d.get(k), list) else [])[:cls.MAX_IDS]:
                if isinstance(x, str) and cls.ID_RE.match(x) and x not in seen:
                    seen.add(x)
                    res.append(x)
            out[k] = res
        out["design"] = d.get("design") if d.get("design") in cls.DESIGNS else ""
        return out

    def _read(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        return d if isinstance(d, dict) else None

    def get(self):
        d = self._read()
        p = (d or {}).get("profiles") if isinstance((d or {}).get("profiles"), dict) else {}
        return {"set": d is not None, **{k: self.clean(p.get(k)) for k in self.PROFILES}}

    def _write(self, profiles):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as f:
            json.dump({"version": 1, "profiles": profiles}, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)

    def put(self, profile, data):
        if profile not in self.PROFILES:
            raise ValueError("Ansicht unbekannt")
        if not isinstance(data, dict):
            raise ValueError("Angaben ungültig")
        with self.lock:
            cur = self.get()
            new = dict(cur[profile])
            for k in ("order", "hidden", "design"):
                if k in data:
                    new[k] = data[k]
            profiles = {k: cur[k] for k in self.PROFILES}
            profiles[profile] = self.clean(new)
            self._write(profiles)
        return self.get()

    def init(self, data):
        if not isinstance(data, dict):
            raise ValueError("Angaben ungültig")
        with self.lock:
            if self._read() is None:
                self._write({k: self.clean(data.get(k)) for k in self.PROFILES})
        return self.get()


class DeckStore:
    MAX_LEN = 500
    SIZE = {"width": (200, 4000, 600), "height": (320, 4000, 600), "mobile_width": (200, 4000, 390), "mobile_height": (320, 4000, 600)}

    @classmethod
    def clean_url(cls, v):
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("Adresse ungültig")
        v = v.strip()
        if not v:
            return ""
        if len(v) > cls.MAX_LEN or any(ord(c) < 33 or ord(c) == 127 for c in v):
            raise ValueError("Adresse ungültig")
        u = urllib.parse.urlsplit(v)
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            raise ValueError("Die Adresse muss mit https:// beginnen.")
        return v

    @classmethod
    def clean_size(cls, k, v):
        lo, hi, std = cls.SIZE[k]
        if v is None or v == "":
            return std
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            raise ValueError("Größe ungültig")
        return int(max(lo, min(hi, round(v))))

    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, "deck.json")
        self.lock = threading.Lock()

    def get(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        d = d if isinstance(d, dict) else {}
        url = ""
        for k in ("url", "desktop", "mobile"):
            try:
                url = self.clean_url(d.get(k))
            except ValueError:
                url = ""
            if url:
                break
        out = {"url": url}
        for k in self.SIZE:
            try:
                out[k] = self.clean_size(k, d.get(k))
            except ValueError:
                out[k] = self.SIZE[k][2]
        return out

    def put(self, data):
        if not isinstance(data, dict):
            raise ValueError("Angaben ungültig")
        new = {}
        if "url" in data:
            new["url"] = self.clean_url(data["url"])
        for k in self.SIZE:
            if k in data:
                new[k] = self.clean_size(k, data[k])
        with self.lock:
            cur = self.get()
            cur.update(new)
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
            with os.fdopen(fd, "w") as f:
                json.dump(cur, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)
        return cur
