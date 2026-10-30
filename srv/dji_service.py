
import json
import os
import re
import socket
import time

from srv.cameras import HDMI_KEY
from srv.netinfo import iface_ips
from srv.util import json_file


DJI_COMMANDS = ("state", "wifi_options", "scan", "add", "update", "use_saved", "delete_saved", "remove",
                "connect", "disconnect", "reconnect", "phone_pair", "phone_pair_stop", "phone_read", "phone_remove", "ctrl_scan", "ctrl_pair", "ctrl_remove")


DJI_FIELDS = ("addr", "name", "model", "kind", "wifi_ifname", "ssid", "password", "ip", "resolution", "fps", "bitrate",
              "stabilization", "autoconnect", "status_only")


class DjiService:
    MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    HOST, PORT = "127.0.0.1", 9101

    PROFILES = {"main": {"resolution": "1080p", "fps": 30, "bitrate": 8000},
                "pip": {"resolution": "720p", "fps": 30, "bitrate": 4000}}

    def __init__(self, state_dir, cams, rtmp_app, rtmp_port, demo=False):
        self.config_path = os.path.join(state_dir, "dji-cameras.json")
        self.token_path = os.path.join(state_dir, "dji-token")
        self.cams, self.app, self.port = cams, rtmp_app, rtmp_port
        self.pipeline = None
        self.demo = demo
        self.fake = {"cameras": {}, "scan": [], "scanning": False} if demo else None
        self._next_id = 1

    def role_for_key(self, key):
        cfg = getattr(self.pipeline, "cfg", None) or {}
        if cfg.get("main") == key:
            return "main"
        if key in (cfg.get("pip"), cfg.get("pip2"), cfg.get("pip3")):
            return "pip"
        cam = next((c for c in self.cams.cams if c["key"] == key), None) if self.cams else None
        return {"main": "main", "pip": "pip"}.get((cam or {}).get("role"))

    def _free_role(self):
        roles = {c["role"] for c in self.cams.cams}
        return "main" if "main" not in roles else "pip" if "pip" not in roles else "extra"

    def _call(self, req, timeout=8):
        if self.demo:
            return self._fake_call(req)
        try:
            with open(self.token_path) as f:
                tok = f.read().strip()
        except OSError:
            raise RuntimeError("Der Bluetooth-Dienst (pipbox-dji) ist noch nicht bereit")
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
                                raise ValueError(str(resp["error"]))
                            return resp
                    chunk = sk.recv(65536)
                    if not chunk:
                        raise RuntimeError("Der Bluetooth-Dienst (pipbox-dji) hat nicht geantwortet")
                    buf += chunk
                    if len(buf) > 1 << 20:
                        raise RuntimeError("Antwort des Bluetooth-Dienstes zu groß")
        except OSError:
            raise RuntimeError("Der Bluetooth-Dienst (pipbox-dji) läuft nicht")

    def _config(self):
        d = json_file(self.config_path)
        return (d.get("cameras") or {}) if isinstance(d, dict) else {}

    def addr_for_key(self, key):
        if not (isinstance(key, str) and key.startswith("dji-")):
            return None
        for addr, cfg in self._config().items():
            if cfg.get("rtmp_key") == key and not cfg.get("status_only"):
                return addr
        return None

    def remove_for_key(self, key):
        addr = self.addr_for_key(key)
        if not addr:
            return False
        try:
            return not self._call({"cmd": "remove", "addr": addr}).get("error")
        except (RuntimeError, ValueError):
            return False

    def fps_for_key(self, key):
        if not key.startswith("dji-"):
            return None
        for cfg in self._config().values():
            if isinstance(cfg, dict) and cfg.get("rtmp_key") == key:
                return cfg.get("fps") if cfg.get("fps") in (25, 30) else 30
        return None

    def host_for_key(self, key):
        if not str(key).startswith("dji-"):
            return None
        for cfg in self._config().values():
            if isinstance(cfg, dict) and cfg.get("rtmp_key") == key:
                w = cfg.get("wifi_ifname")
                if not w:
                    return None
                if w == "manual":
                    return (cfg["ip"], "manual") if cfg.get("ip") else None
                for o in iface_ips():
                    if o["iface"] == w:
                        return (o.get("cam_ip") or o["ip"], w)
                return None
        return None

    EXTRAS_TTL = 3.0

    def camera_extras(self):
        now = time.monotonic()
        hit = getattr(self, "_extras", None)
        if hit and now - hit[0] < self.EXTRAS_TTL:
            return hit[1]
        out, parked = {}, set()
        try:
            for c in self._call({"cmd": "state"}, timeout=2).get("cameras", []):
                if c.get("rtmp_key") and c.get("battery") is not None:
                    out[c["rtmp_key"]] = {"battery": c["battery"], "battery_age": c.get("battery_age"), "charging": c.get("charging")}
                sends = c.get("state") in ("streaming", "status") or (c.get("publishing") is True and not c.get("manual_off"))
                if c.get("rtmp_key") and (c.get("manual_off") or not c.get("autoconnect")) and not sends:
                    parked.add(c["rtmp_key"])
        except (RuntimeError, ValueError):
            pass
        self._extras = (now, out)
        self._parked = parked
        return out

    def camera_kinds(self):
        out = {}
        try:
            for c in self._call({"cmd": "state"}, timeout=2).get("cameras", []):
                if c.get("rtmp_key") and c.get("kind"):
                    out[c["rtmp_key"]] = c["kind"]
        except (RuntimeError, ValueError):
            pass
        return out

    def parked_keys(self):
        self.camera_extras()
        return set(getattr(self, "_parked", set()))

    def _ensure_listed(self, cameras):
        if not self.cams:
            return
        have = {c["key"] for c in self.cams.cams}
        for c in cameras:
            key = c.get("rtmp_key")
            if key and key not in have and key != HDMI_KEY:
                try:
                    self.cams.add(c.get("name") or c.get("model") or "DJI-Kamera", key, self._free_role())
                    have.add(key)
                except ValueError:
                    pass

    def status(self):
        out = {"available": False, "reason": "", "cameras": [], "scan": [], "scanning": False, "scan_error": "",
               "wifi_options": [], "adapters": [], "adapter_problems": [], "driver": {}, "bleak": True,
               "phones": [], "phone_pairing": {}, "controllers": [], "ctrl_found": [], "ctrl_scan": {}, "ctrl_message": ""}
        try:
            out.update(self._call({"cmd": "state"}))
            out["wifi_options"] = self._call({"cmd": "wifi_options"}).get("wifi_options", [])
            ad = self._call({"cmd": "adapters"})
            out.update({k: ad[k] for k in ("adapters", "adapter_problems", "driver") if k in ad})
            out["available"] = True
            self._ensure_listed(out["cameras"])
        except RuntimeError as e:
            out["reason"] = str(e)
        except ValueError as e:
            out["reason"] = str(e)
        return out

    def command(self, d):
        cmd = d.get("cmd")
        if cmd not in DJI_COMMANDS:
            raise ValueError("Unbekannter Befehl")
        req = {"cmd": cmd}
        for k in DJI_FIELDS:
            if k in d:
                req[k] = d[k]
        if cmd == "use_saved" or cmd == "delete_saved":
            req["ssid"] = d.get("ssid", "")
        if "addr" in req and not self.MAC_RE.match(str(req["addr"])):
            raise ValueError("Ungültige Geräteadresse")
        if cmd == "add":
            key = "dji-" + str(req.get("addr", "")).replace(":", "").lower()[-6:]
            role = self.role_for_key(key) or self._free_role()
            req["settings"] = dict(self.PROFILES.get(role, {}))
        if cmd == "update" and "name" in req and self.cams:
            cfg = self._config().get(str(req.get("addr", "")).upper()) or {}
            cam = next((c for c in self.cams.cams if c["key"] == cfg.get("rtmp_key")), None)
            if cfg.get("status_only"):
                cam = None
            if cam and str(req["name"]).strip() and cam["name"] != str(req["name"]).strip()[:40]:
                self.cams.update(cam["id"], name=str(req["name"]))
        gone_key = None
        if cmd == "remove" and self.cams:
            cfg = self._config().get(str(req.get("addr", "")).upper()) or {}
            if not cfg.get("status_only"):
                gone_key = cfg.get("rtmp_key")
        old_key = None
        if cmd == "update" and req.get("status_only") is True and self.cams:
            old_key = (self._config().get(str(req.get("addr", "")).upper()) or {}).get("rtmp_key")
        res = self._call(req)
        if old_key and old_key.startswith("dji-") and not res.get("error"):
            cam = next((c for c in self.cams.cams if c["key"] == old_key), None)
            if cam:
                self.cams.remove(cam["id"])
        if gone_key and not res.get("error") and self.cams:
            cam = next((c for c in self.cams.cams if c["key"] == gone_key), None)
            if cam:
                self.cams.remove(cam["id"])
            self.cams.forget(gone_key)
        if cmd == "add" and res.get("key") and self.cams:
            self._ensure_listed([{"rtmp_key": res["key"], "model": req.get("model"), "name": req.get("name")}])
        return {k: v for k, v in res.items() if k not in ("reply_to", "token")}

    def _fake_call(self, req):
        f, cmd = self.fake, req.get("cmd")
        if not f["cameras"] and not f.get("seeded"):
            f["seeded"] = True
            base = {"wifi_ifname": "eth1", "ssid": "", "ip": "192.168.80.1", "resolution": "1080p", "fps": 30, "bitrate": 8000,
                    "stabilization": "off", "autoconnect": True, "saved": ["KameraNetz"], "in_range": True, "retry_in": 0,
                    "publishing": False, "locked": False, "detail": "", "battery": None}
            f["cameras"]["D0:D0:4B:00:00:01"] = dict(base, addr="D0:D0:4B:00:00:01", name="Kamera vorn", model="Osmo Action 5 Pro",
                                                     kind="action5", rtmp_key="dji-000001", state="streaming", battery=82, charging=True, battery_age=3,
                                                     publishing=True, locked=True, detail="rtmp://192.168.80.1:1935/publish/dji-000001")
            f["cameras"]["F0:4F:E2:00:00:02"] = dict(base, addr="F0:4F:E2:00:00:02", name="Kamera hinten", model="Osmo Pocket 3",
                                                     kind="pocket3", rtmp_key="dji-000002", state="error", autoconnect=False,
                                                     resolution="720p", bitrate=4000, retry_in=0, battery=18, battery_age=95, charging=False,
                                                     detail="Kamera nicht gefunden. Ist sie an, Bluetooth aktiv und nicht mit dem Handy verbunden?")
        if cmd == "state":
            pair = f.get("pair_until", 0) - time.time()
            if f.get("pair_until") and pair <= 0 and not f.get("phones_seeded"):
                f["phones_seeded"] = True
                f.setdefault("phones", []).append({"addr": "7C:A1:77:00:00:09", "name": "Pixel von Marco", "percent": 60, "steps": 5,
                                                   "age": 0, "error": "", "reading": False})
            return {"cameras": list(f["cameras"].values()), "scan": f["scan"], "scanning": f["scanning"], "scan_error": "",
                    "bleak": True, "phones": f.get("phones", []),
                    "phone_pairing": {"active": pair > 0, "left": max(0, int(pair)), "message": ""},
                    "controllers": f.get("controllers", []),
                    "ctrl_scan": {"active": 0 < f.get("ctrl_scan_until", 0) - time.time(), "left": max(0, int(f.get("ctrl_scan_until", 0) - time.time())), "message": ""},
                    "ctrl_found": [] if f.get("ctrl_scan_until", 0) - time.time() > 0 or not f.get("ctrl_scan_until") else
                    [r for r in ({"addr": "E4:11:22:33:44:55", "name": "Mini Controller", "rssi": -48, "input": True, "paired": False},
                                 {"addr": "AA:BB:CC:00:11:22", "name": "Jabra Evolve2 85", "rssi": -70, "input": False, "paired": False})
                     if r["addr"] not in {c["addr"] for c in f.get("controllers", [])}],
                    "ctrl_message": ""}
        if cmd == "ctrl_scan":
            f["ctrl_scan_until"] = time.time() + 5
            return {"ok": True}
        if cmd == "ctrl_pair":
            f.setdefault("controllers", []).append({"addr": str(req.get("addr", "")).upper(), "name": "Mini Controller", "connected": True, "battery": 87, "pairing": False})
            return {"ok": True}
        if cmd == "ctrl_remove":
            f["controllers"] = [c for c in f.get("controllers", []) if c["addr"] != str(req.get("addr", "")).upper()]
            return {"ok": True}
        if cmd == "phone_pair":
            f["pair_until"] = time.time() + 6
            return {"ok": True}
        if cmd == "phone_pair_stop":
            f["pair_until"] = 0
            return {"ok": True}
        if cmd in ("phone_read", "phone_remove"):
            ph = f.get("phones", [])
            hit = next((p for p in ph if p["addr"] == str(req.get("addr", "")).upper()), None)
            if hit is None:
                raise ValueError("Unbekanntes Handy")
            if cmd == "phone_remove":
                ph.remove(hit)
            else:
                hit.update(percent=max(0, hit["percent"] - 20) if hit["percent"] else 80, age=0, error="")
            return {"ok": True}
        if cmd == "wifi_options":
            return {"wifi_options": [
                {"ifname": "wlan0", "ssid": "Handy-Hotspot", "ip": "10.1.1.20", "type": "client", "secret_missing": False},
                {"ifname": "eth0", "ssid": "", "ip": "192.168.1.20", "type": "other", "secret_missing": False},
                {"ifname": "eth1", "ssid": "", "ip": "192.168.80.1", "type": "other", "secret_missing": False}]}
        if cmd == "adapters":
            return {"adapters": [{"usb_id": "0b05:190e", "name": "ASUS USB-BT500", "vendor": "Realtek", "address": "A0:AD:9F:00:00:00", "powered": True}],
                    "adapter_problems": [], "driver": {}}
        if cmd == "scan":
            f["scan"] = [{"addr": "AA:BB:CC:00:00:03", "name": "OsmoAction6", "model": "Osmo Action 6", "kind": "action6",
                          "rssi": -61, "paired": False}]
            return {"ok": True}
        addr = str(req.get("addr", "")).upper()
        if cmd == "add":
            f["cameras"][addr] = {"addr": addr, "name": req.get("name") or req.get("model") or addr, "model": req.get("model", ""),
                                  "kind": req.get("kind", ""), "wifi_ifname": "", "ssid": "", "ip": "", "saved": [],
                                  "rtmp_key": "dji-" + addr.replace(":", "").lower()[-6:], "autoconnect": False,
                                  "state": "idle", "detail": "", "battery": None, "in_range": True, "retry_in": 0,
                                  "publishing": False, "locked": False, **{**{"resolution": "1080p", "fps": 30, "bitrate": 6000,
                                                                           "stabilization": "off"}, **(req.get("settings") or {})}}
            return {"ok": True, "key": f["cameras"][addr]["rtmp_key"]}
        cam = f["cameras"].get(addr)
        if cam is None:
            raise ValueError("Unbekannte Kamera")
        if cmd == "update":
            for k in DJI_FIELDS:
                if k in req and k != "addr" and not (k == "password" and not req[k]):
                    cam[k] = req[k]
        elif cmd == "remove":
            del f["cameras"][addr]
        elif cmd in ("connect", "reconnect"):
            if cam.get("status_only"):
                cam.update(state="status", detail="Nur Akkustand per Bluetooth", locked=True, battery=64)
            else:
                cam.update(state="streaming", detail="Die Kamera streamt (Vorschau)", publishing=True, locked=True, battery=64)
        elif cmd == "disconnect":
            cam.update(state="idle", detail="", publishing=False, locked=False, battery=None)
        elif cmd == "delete_saved":
            cam["saved"] = [n for n in cam.get("saved", []) if n != req.get("ssid")]
        return {"ok": True}
