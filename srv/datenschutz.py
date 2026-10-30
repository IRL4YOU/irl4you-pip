
import hashlib
import json
import os
import re
import threading
import time
import urllib.request
from srv.util import BASE_DIR


DATENSCHUTZ_DATEI = "/var/lib/pipbox/datenschutz.json"


DATENSCHUTZ_PY = "/opt/pipbox/privacy/venv/bin/python"


DATENSCHUTZ_MODELL = "/opt/pipbox/privacy/modelle/kopf_fp16.rknn"


DATENSCHUTZ_BAUSTEIN = "/opt/pipbox/gst/libgstpbpixel.so"


PRIVACY_URL = "http://127.0.0.1:8790/"


PBPIXEL_TAP, PBPIXEL_BOXES = "/dev/shm/pbpixel-tap", "/dev/shm/pbpixel-boxes"


SENDE_PIPELINE = "/var/tmp/pipbox/pipeline"


def kette_signatur(text):
    return ("pbpixelmask" in text, sorted(re.findall(r"min-threshold-time=(\d+)", text)))


def neustart_noetig(build_text, pfad=None):
    try:
        with open(pfad or SENDE_PIPELINE) as f:
            laufend = f.read()
    except OSError:
        return False
    return bool(build_text) and kette_signatur(laufend) != kette_signatur(build_text)


def pixel_stand(store):
    c = store.config()
    return {"an": c["an"], "kopf": c["kopf"], "kennz": c["kennz"]}


def pixel_umschalten(store, send, pipeline, an, kopf, kennz):
    try:
        store.put({"an": an, "kopf": kopf, "kennz": kennz})
    except ValueError as e:
        return False, str(e)
    if send._active() and neustart_noetig(pipeline.build()):
        restarted, note = send.restart_if_live()
        return True, "Sendung startet kurz neu" if restarted else note
    return True, ""


def privacy_status():
    try:
        with urllib.request.urlopen(PRIVACY_URL, timeout=0.6) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return {"up": False}
    if not isinstance(d, dict):
        return {"up": False}
    out = {"up": True}
    for k in ("pass_s", "slow", "temp", "warn", "module", "boxes", "paket", "cpu_gross", "cpu_klein", "paused"):
        if k in d:
            out[k] = d[k]
    return out


class DatenschutzStore:
    DEFAULT = {"an": False, "kopf": True, "kennz": True, "zweit": True, "delay_ms": 2000, "zonen": [], "lizenz_ok": False, "lizenz_sha": ""}
    MAX_ZONEN = 16
    MIN_DELAY, MAX_DELAY = 2000, 12000

    def __init__(self, state_dir, lizenz_pfad=None):
        self.path = os.path.join(state_dir, "datenschutz.json")
        self.lizenz_pfad = lizenz_pfad or os.path.join(BASE_DIR, "LICENSE-ERKENNUNG.md")
        self.lock = threading.Lock()

    @classmethod
    def clean(cls, d):
        d = d if isinstance(d, dict) else {}
        out = dict(cls.DEFAULT)
        for k in ("an", "kopf", "kennz", "zweit", "lizenz_ok"):
            if isinstance(d.get(k), bool):
                out[k] = d[k]
        try:
            out["delay_ms"] = max(cls.MIN_DELAY, min(cls.MAX_DELAY, int(d.get("delay_ms", cls.DEFAULT["delay_ms"]))))
        except (TypeError, ValueError):
            pass
        zonen = []
        for z in (d.get("zonen") if isinstance(d.get("zonen"), list) else [])[:cls.MAX_ZONEN]:
            try:
                z = [round(float(v), 4) for v in z]
            except (TypeError, ValueError):
                continue
            if len(z) == 4 and all(0.0 <= v <= 1.0 for v in z) and z[2] - z[0] >= 0.01 and z[3] - z[1] >= 0.01:
                zonen.append(z)
        out["zonen"] = zonen
        out["lizenz_sha"] = d["lizenz_sha"] if isinstance(d.get("lizenz_sha"), str) and re.fullmatch(r"[0-9a-f]{64}", d["lizenz_sha"]) else ""
        return out

    def lizenz_text(self):
        try:
            with open(self.lizenz_pfad, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def lizenz_sha(self):
        t = self.lizenz_text()
        return hashlib.sha256(t.encode("utf-8")).hexdigest() if t else ""

    @staticmethod
    def installiert():
        return os.path.exists(DATENSCHUTZ_PY) and os.path.exists(DATENSCHUTZ_MODELL) and os.path.exists(DATENSCHUTZ_BAUSTEIN)

    ADDON_STATUS = "/run/pipbox-addon/status.json"
    ADDON_PATH_UNIT = "/etc/systemd/system/pipbox-addon.path"
    ADDON_DATEIEN = "/opt/pipbox/addons/datenschutz/install/install-datenschutz.sh"

    def addon(self):
        out = {"state": "idle", "action": "", "step": "", "message": "", "progress": 0,
               "helper": os.path.exists(self.ADDON_PATH_UNIT), "dateien": os.path.exists(self.ADDON_DATEIEN)}
        try:
            with open(self.ADDON_STATUS) as f:
                st = json.load(f)
        except (OSError, ValueError):
            return out
        if isinstance(st, dict):
            for k in ("state", "action", "step", "message"):
                if isinstance(st.get(k), str):
                    out[k] = st[k][:200]
            if isinstance(st.get("progress"), (int, float)):
                out["progress"] = max(0, min(100, int(st["progress"])))
            t = st.get("time")
            if out["state"] != "installing" and isinstance(t, (int, float)) and time.time() - t > 6 * 3600:
                out.update(state="idle", message="", action="")
        return out

    def addon_request(self, action, sending):
        if action not in ("install", "remove"):
            raise ValueError("Unbekannte Aktion")
        a = self.addon()
        if not a["helper"]:
            raise ValueError("Der Add-on-Helfer ist nicht installiert (Software-Update ausführen)")
        if a["state"] == "installing":
            raise ValueError("Eine Einrichtung läuft bereits")
        if sending:
            raise ValueError("Es wird gerade gesendet. Bitte zuerst die Übertragung beenden.")
        if action == "install":
            if not self.config()["lizenz_ok"]:
                raise ValueError("Zuerst die Lizenz der Erkennung annehmen")
            if self.installiert():
                raise ValueError("Die Erkennung ist schon eingerichtet")
            if not a["dateien"]:
                raise ValueError("Die Add-on-Dateien fehlen (Software-Update ausführen)")
        elif not self.installiert():
            raise ValueError("Die Erkennung ist nicht eingerichtet")
        req = os.path.join(os.path.dirname(self.path), "addon-request")
        fd = os.open(req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("datenschutz-" + action + "\n")

    def _read(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def config(self):
        c = self.clean(self._read())
        if c["lizenz_ok"] and c["lizenz_sha"] != self.lizenz_sha():
            c["lizenz_ok"] = False
        return c

    def aktiv(self):
        c = self.config()
        return bool(c["an"] and c["lizenz_ok"] and self.installiert())

    def get(self):
        c = self.config()
        return {"config": c, "aktiv": bool(c["an"] and c["lizenz_ok"] and self.installiert()), "installiert": self.installiert(),
                "lizenz_sha": self.lizenz_sha(), "status": privacy_status() if c["an"] and c["lizenz_ok"] else {"up": False}, "addon": self.addon()}

    def _write(self, c):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w") as f:
            json.dump(c, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)

    def put(self, req):
        if not isinstance(req, dict):
            raise ValueError("Angaben ungültig")
        with self.lock:
            cur = self.config()
            new = dict(cur)
            for k in ("an", "kopf", "kennz", "zweit", "delay_ms", "zonen"):
                if k in req:
                    new[k] = req[k]
            if "lizenz_ok" in req:
                if req["lizenz_ok"] is True:
                    if req.get("lizenz_sha") != self.lizenz_sha() or not self.lizenz_sha():
                        raise ValueError("Lizenz: Bitte den aktuellen Text laden und erneut annehmen")
                    new["lizenz_ok"], new["lizenz_sha"] = True, self.lizenz_sha()
                else:
                    new["lizenz_ok"], new["lizenz_sha"], new["an"] = False, "", False
            new = self.clean(new)
            if new["an"] and not new["lizenz_ok"]:
                raise ValueError("Zuerst die Lizenz der Erkennung annehmen")
            if new["an"] and not self.installiert():
                raise ValueError("Die Erkennung ist auf dieser Box nicht eingerichtet (zuerst einrichten)")
            if not new["kopf"] and not new["kennz"]:
                raise ValueError("Mindestens Köpfe oder Kennzeichen einschalten")
            self._write(new)
        return self.get()


def datenschutz_aktiv(pfad=None):
    pfad = pfad or DATENSCHUTZ_DATEI
    try:
        with open(pfad) as f:
            c = DatenschutzStore.clean(json.load(f))
    except (OSError, ValueError):
        return None
    if not (c["an"] and c["lizenz_ok"] and DatenschutzStore.installiert()):
        return None
    return {"delay_ms": c["delay_ms"]}
