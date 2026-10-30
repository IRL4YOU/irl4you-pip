
import hashlib
import json
import os
import re
import secrets
import threading


HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


class SrtlaStore:
    DEFAULT_SETTINGS = {"min_kbps": 450, "max_kbps": 12000, "latency_ms": 4000, "uplinks": ["eth0", "eth1"],
                        "spread": "best"}

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"servers": [], "selected": None, "settings": dict(self.DEFAULT_SETTINGS)}
        try:
            with open(path) as f:
                loaded = json.load(f)
            self.data.update({k: loaded[k] for k in ("servers", "selected") if k in loaded})
            self.data["settings"].update(loaded.get("settings", {}))
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def check(req):
        name = str(req.get("name", "")).strip()
        host = str(req.get("host", "")).strip().lower()
        sid = str(req.get("streamid", "")).strip()
        try:
            port = int(req.get("port"))
        except (TypeError, ValueError):
            raise ValueError("Port: Zahl von 1 bis 65535")
        if not 1 <= len(name) <= 40:
            raise ValueError("Name: 1 bis 40 Zeichen")
        if not HOST_RE.match(host):
            raise ValueError("Adresse: nur Buchstaben, Ziffern, Punkt und Bindestrich (ohne rtmp:// oder Leerzeichen)")
        if not 1 <= port <= 65535:
            raise ValueError("Port: Zahl von 1 bis 65535")
        if len(sid) > 512 or any(ord(c) < 32 or ord(c) > 126 for c in sid):
            raise ValueError("Stream-ID: höchstens 512 sichtbare Zeichen")
        return {"name": name, "host": host, "port": port, "streamid": sid}

    def public(self):
        with self.lock:
            return {"servers": [{"id": s["id"], "name": s["name"], "host": s["host"], "port": s["port"],
                                 "has_streamid": bool(s.get("streamid"))} for s in self.data["servers"]],
                    "selected": self.data["selected"], "settings": dict(self.data["settings"])}

    def streamid(self, sid):
        with self.lock:
            cur = next((s for s in self.data["servers"] if s["id"] == sid), None)
            if not cur:
                raise KeyError(sid)
            return cur.get("streamid", "")

    def add(self, req):
        item = self.check(req)
        with self.lock:
            item["id"] = secrets.token_hex(4)
            self.data["servers"].append(item)
            if not self.data["selected"]:
                self.data["selected"] = item["id"]
            self.save()
        return item["id"]

    def update(self, sid, req):
        with self.lock:
            cur = next((s for s in self.data["servers"] if s["id"] == sid), None)
            if not cur:
                raise KeyError(sid)
            merged = {"name": cur["name"], "host": cur["host"], "port": cur["port"],
                      "streamid": cur.get("streamid", "")}
            merged.update({k: v for k, v in req.items() if k in merged and not (k == "streamid" and v in (None, ""))})
            if req.get("clear_streamid"):
                merged["streamid"] = ""
            cur.update(self.check(merged))
            self.save()

    def remove(self, sid):
        with self.lock:
            n = len(self.data["servers"])
            self.data["servers"] = [s for s in self.data["servers"] if s["id"] != sid]
            if len(self.data["servers"]) == n:
                raise KeyError(sid)
            if self.data["selected"] == sid:
                self.data["selected"] = self.data["servers"][0]["id"] if self.data["servers"] else None
            self.save()

    def select(self, sid):
        with self.lock:
            if not any(s["id"] == sid for s in self.data["servers"]):
                raise ValueError("Server nicht gefunden")
            self.data["selected"] = sid
            self.save()

    def set_settings(self, req, valid_ifaces):
        with self.lock:
            cur = dict(self.data["settings"])
        try:
            mn = int(req.get("min_kbps", cur.get("min_kbps")))
            mx = int(req.get("max_kbps", cur.get("max_kbps")))
            lat = int(req.get("latency_ms", cur.get("latency_ms")))
        except (KeyError, TypeError, ValueError):
            raise ValueError("Bitrate und Latenz müssen Zahlen sein")
        if not 100 <= mn < mx <= 20000:
            raise ValueError("Bitrate: Minimum ab 100, kleiner als Maximum, Maximum höchstens 20000 kbit/s")
        if not 100 <= lat <= 10000:
            raise ValueError("Latenz: 100 bis 10000 ms")
        ups = [u for u in req.get("uplinks", []) if isinstance(u, str)]
        if any(not re.fullmatch(r"[A-Za-z0-9._-]{1,15}", u) for u in ups):
            raise ValueError("Ungültiger Name eines Netzwerks")
        with self.lock:
            before = set(self.data["settings"].get("uplinks", []))
        if any(u not in valid_ifaces and u not in before for u in ups) or not any(u in valid_ifaces for u in ups):
            raise ValueError("Mindestens ein vorhandenes Netzwerk als Sendeweg wählen")
        spread = req.get("spread", cur.get("spread", "best"))
        if spread not in ("best", "all"):
            raise ValueError("Verteilung: beste Leitung bevorzugen oder alle gleichzeitig")
        with self.lock:
            self.data["settings"] = {"min_kbps": mn, "max_kbps": mx, "latency_ms": lat, "uplinks": sorted(set(ups)),
                                     "spread": spread}
            self.save()


PENDING_LABELS = (("server", "SRTLA-Server"), ("bitrate", "Bitrate"), ("latency", "Latenz"), ("spread", "Verteilung"))


def srtla_signature(data):
    cur = next((x for x in data.get("servers", []) if x.get("id") == data.get("selected")), None) or {}
    st = {**SrtlaStore.DEFAULT_SETTINGS, **data.get("settings", {})}

    def h(*a):
        return hashlib.sha256(json.dumps(a, sort_keys=True).encode()).hexdigest()[:12]
    try:
        mn, mx, lat = int(st["min_kbps"]), int(st["max_kbps"]), int(st["latency_ms"])
    except (TypeError, ValueError):
        mn = mx = lat = 0
    return {"server": h(cur.get("id"), cur.get("host"), cur.get("port"), cur.get("streamid")),
            "bitrate": h(mn, mx), "latency": h(lat), "spread": h("all" if st.get("spread") == "all" else "best")}
