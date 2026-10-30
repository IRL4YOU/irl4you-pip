
import json
import os
import socket
import threading
import time
import hdmi_daemon

from srv.cameras import HDMI_KEY


class HdmiService:
    HOST, PORT = "127.0.0.1", 9102
    CAMERA_NAMES = ("HDMI", "HDMI-Eingang", "HDMI 2")
    USB_NAMES = ("USB-Kamera", "USB-Webcam", "USB 2")
    ALLOWED = ("enabled", "bitrate", "fps", "audio", "source", "usb_format")
    TTL = 1.5
    DOWN = "Der HDMI-Dienst läuft nicht (Software-Update oder install.sh ausführen)"

    def __init__(self, state_dir, cams, demo=False):
        self.token_path = os.path.join(state_dir, "hdmi-token")
        self.cams, self.demo = cams, demo
        self.lock = threading.Lock()
        self._next_id = 0
        self._hit = (0.0, None)
        self.fake = dict(hdmi_daemon.DEFAULTS) if demo else None

    def _call(self, req, timeout=4):
        try:
            with open(self.token_path) as f:
                tok = f.read().strip()
        except OSError:
            raise RuntimeError(self.DOWN)
        with self.lock:
            rid = self._next_id = self._next_id + 1
        line = (json.dumps(dict(req, token=tok, id=rid)) + "\n").encode()
        try:
            with socket.create_connection((self.HOST, self.PORT), timeout=timeout) as sk:
                sk.settimeout(timeout)
                sk.sendall(line)
                buf = b""
                while True:
                    while b"\n" in buf:
                        one, buf = buf.split(b"\n", 1)
                        try:
                            resp = json.loads(one)
                        except ValueError:
                            continue
                        if isinstance(resp, dict) and resp.get("reply_to") == rid:
                            if resp.get("error"):
                                if resp["error"] == "kein Zugriff":
                                    raise RuntimeError(self.DOWN)
                                raise ValueError(str(resp["error"])[:120])
                            return resp
                    chunk = sk.recv(65536)
                    if not chunk:
                        raise RuntimeError(self.DOWN)
                    buf += chunk
                    if len(buf) > 1 << 20:
                        raise RuntimeError(self.DOWN)
        except OSError:
            raise RuntimeError(self.DOWN)

    def _fake_status(self):
        f = self.fake
        usb = f.get("source") == "usb"
        return {"ok": True, "available": True, "state": "streaming" if f["enabled"] else "off", "message": "", "settings": dict(f),
                "signal": ({"plugged": True, "locked": True, "width": 1280, "height": 720, "fps": 30.0, "interlaced": False, "format": "", "depth": 0} if usb else
                           {"plugged": True, "locked": True, "width": 1920, "height": 1080, "fps": 60.0, "interlaced": False, "format": "RGB", "depth": 8}),
                "usb": ({"present": True, "name": "Beispiel-Webcam", "node": "/dev/video2", "format": "MJPEG 1280x720@30", "audio": "plughw:CARD=Beispiel"} if usb else
                        {"present": False, "name": "", "node": "", "format": "", "audio": ""}),
                "signal_known": True, "publishing": bool(f["enabled"]), "restarts": 0}

    def _listed(self):
        return bool(self.cams) and any(c["key"] == HDMI_KEY for c in self.cams.cams)

    def ensure_listed(self, source="hdmi"):
        if not self.cams or self._listed():
            return
        roles = {c["role"] for c in self.cams.cams}
        role = "main" if "main" not in roles else "pip" if "pip" not in roles else "extra"
        taken = {c["name"].casefold() for c in self.cams.cams}
        names = self.USB_NAMES if source == "usb" else self.CAMERA_NAMES
        name = next((n for n in names if n.casefold() not in taken), "USB-Kamera 2" if source == "usb" else "HDMI-Kamera")
        try:
            self.cams.add(name, HDMI_KEY, role)
        except ValueError:
            pass

    def status(self):
        now = time.monotonic()
        with self.lock:
            if self._hit[1] is not None and now - self._hit[0] < self.TTL:
                return dict(self._hit[1])
        out = {"service": False, "available": None, "state": "down", "message": self.DOWN, "settings": {k: hdmi_daemon.DEFAULTS[k] for k in self.ALLOWED}, "signal": {},
               "usb": {}, "signal_known": False, "publishing": None, "restarts": 0}
        try:
            resp = self._fake_status() if self.demo else self._call({"cmd": "status"})
            for k in ("available", "state", "message", "signal", "usb", "signal_known", "publishing", "restarts"):
                if k in resp:
                    out[k] = resp[k]
            st = resp.get("settings") if isinstance(resp.get("settings"), dict) else {}
            out["settings"] = {k: st.get(k, hdmi_daemon.DEFAULTS[k]) for k in self.ALLOWED}
            out["service"] = True
            if out["settings"]["enabled"]:
                self.ensure_listed(out["settings"].get("source", "hdmi"))
        except (RuntimeError, ValueError) as e:
            out["message"] = str(e) or self.DOWN
        out["listed"] = self._listed()
        with self.lock:
            self._hit = (now, out)
        return dict(out)

    def set(self, req):
        if not isinstance(req, dict):
            raise ValueError("Ungültige Anfrage")
        settings = {k: req[k] for k in self.ALLOWED if k in req}
        hdmi_daemon.clean_settings(settings, self.fake if self.demo else None)
        if self.demo:
            self.fake = hdmi_daemon.clean_settings(settings, self.fake)
        elif settings:
            self._call({"cmd": "set", "settings": settings})
        if settings.get("enabled") is True:
            self.ensure_listed(settings.get("source") or (self.fake or {}).get("source") or self._last_source())
        with self.lock:
            self._hit = (0.0, None)
        return self.status()

    def _last_source(self):
        with self.lock:
            st = self._hit[1] or {}
        return (st.get("settings") or {}).get("source", "hdmi")

    def on_camera_removed(self):
        try:
            self.set({"enabled": False})
        except (RuntimeError, ValueError):
            pass
