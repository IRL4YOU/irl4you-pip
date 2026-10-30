
import json
import os
import re
import secrets
import stat
import subprocess
import threading
import time
import dji

from srv.netinfo import iface_ips, mac_suffix
from srv.util import json_file, read, ttl_cached


class DeviceNames:
    KEY_RE = re.compile(r"^(usb:[0-9a-f]{4}:[0-9a-f]{4}|if:[a-z0-9]{2,15}|net:[a-z0-9]{2,15}|bt:([0-9A-F]{2}:){5}[0-9A-F]{2})$")

    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, "device-names.json")
        self.lock = threading.Lock()

    def _all(self):
        d = json_file(self.path)
        return {k: v for k, v in d.items() if isinstance(k, str) and self.KEY_RE.match(k) and isinstance(v, str)} if isinstance(d, dict) else {}

    @staticmethod
    def key(usb_id="", iface="", address=""):
        if usb_id:
            return "usb:" + usb_id
        if iface:
            return "if:" + iface
        return "bt:" + address.upper() if address else ""

    def label(self, key, default):
        return self._all().get(key) or default

    def conn_names(self):
        return {k[4:]: v for k, v in self._all().items() if k.startswith("net:") and v}

    def set(self, key, name):
        if not isinstance(key, str) or not self.KEY_RE.match(key):
            raise ValueError("Gerät unbekannt")
        if not isinstance(name, str):
            raise ValueError("Name ungültig")
        name = " ".join(name.split())
        if len(name) > 40 or any(not ch.isprintable() for ch in name):
            raise ValueError("Name: höchstens 40 Zeichen, keine Sonderzeichen")
        with self.lock:
            d = self._all()
            if name:
                d[key] = name
            else:
                d.pop(key, None)
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
            with os.fdopen(fd, "w") as f:
                json.dump(d, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)


def label_bluetooth(st, names):
    for a in st.get("adapters") or []:
        a["key"] = DeviceNames.key(a.get("usb_id", ""), "", a.get("address", ""))
        default = dji.device_label(a.get("name", ""), a.get("vendor", ""), a.get("usb_id", "")) or ("Eingebauter Bluetooth-Adapter" if not a.get("usb_id") else "Bluetooth-Stick")
        a["label"] = names.label(a["key"], default) if names and a["key"] else default
        a["custom"] = a["label"] != default
    for x in st.get("adapter_problems") or []:
        x["key"] = DeviceNames.key(x.get("id", ""))
        default = dji.device_label(x.get("name", ""), "", x.get("id", ""))
        x["label"] = names.label(x["key"], default) if names and x["key"] else default
    return st


class Wifi:
    STATUS = "/run/pipbox-wifi/status.json"
    DRIVER_STATUS = "/run/pipbox-wlandriver/status.json"
    ACTIONS = ("scan", "connect", "forget", "disconnect", "hotspot_start", "hotspot_stop", "hotspot_save", "hotspot_delete")
    HS_PREFIX = "pipbox-hotspot-"
    HS_BANDS = {"bg": tuple(range(1, 14)), "a": (36, 40, 44, 48)}

    def __init__(self, state_dir, demo, netchoice, names=None, srtla=None):
        self.req = os.path.join(state_dir, "wifi-request")
        self.hs_file = os.path.join(state_dir, "hotspot.json")
        self.demo, self.netchoice, self.names, self.srtla = demo, netchoice, names, srtla
        self.fake_hs = {}
        self.fake_msg = ("", "")
        self.lock = threading.Lock()

    def hotspots(self):
        if self.demo:
            return dict(self.fake_hs)
        try:
            with open(self.hs_file) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(d, dict) else {}

    def hotspot_secret(self, iface):
        h = self.hotspots().get(iface if isinstance(iface, str) else "")
        if not h or not h.get("password"):
            raise ValueError("Auf dieser Karte ist kein Hotspot gespeichert")
        return {"iface": iface, "ssid": str(h.get("ssid", "")), "password": str(h["password"])}

    @staticmethod
    def caps(iface):
        def raw():
            try:
                r = subprocess.run(["nmcli", "-g", "WIFI-PROPERTIES.AP,WIFI-PROPERTIES.2GHZ,WIFI-PROPERTIES.5GHZ", "dev", "show", iface],
                                   capture_output=True, text=True, timeout=5)
                v = [x.strip().lower() == "yes" for x in r.stdout.splitlines()]
            except (OSError, subprocess.TimeoutExpired):
                return None
            return v + [False] * (3 - len(v)) if v else None
        v = ttl_cached("wifi_caps_" + iface, 300.0, raw)
        return {"ap": v[0], "band24": v[1], "band5": v[2]} if v else {"ap": None, "band24": None, "band5": None}

    def cards(self):
        out = []
        try:
            names = sorted(os.listdir("/sys/class/net"))
        except OSError:
            return out
        ips = {o["iface"]: o["ip"] for o in iface_ips()}
        hs = self.hotspots()
        for n in names:
            if os.path.isdir(f"/sys/class/net/{n}/wireless") and not n.startswith("p2p"):
                info = dji.netdev_info(n)
                out.append({"iface": n, "ip": ips.get(n, ""), "camera_net": n == self.netchoice.iface,
                            "up": (read(f"/sys/class/net/{n}/operstate", "") or "").strip() == "up",
                            "ssid": "", "signal": None, "name": info["name"], "vendor": info["vendor"],
                            "usb_id": info["usb_id"], "driver": info["driver"], "mac4": mac_suffix(n), **self.caps(n)})
                self._name(out[-1])
        if out:
            try:
                r = subprocess.run(["nmcli", "-t", "-f", "DEVICE,CONNECTION", "dev"], capture_output=True, text=True, timeout=4)
                for line in r.stdout.splitlines():
                    dev, _, con = line.partition(":")
                    con = con.replace("\\:", ":")
                    for c in out:
                        if c["iface"] == dev and c["ip"]:
                            if con.startswith(self.HS_PREFIX):
                                c["hotspot_running"] = True
                                c["hotspot_con"] = con
                            else:
                                c["ssid"] = con
                for c in out:
                    if c["ssid"]:
                        r = subprocess.run(["nmcli", "-t", "-f", "IN-USE,SIGNAL", "dev", "wifi", "list", "ifname", c["iface"],
                                            "--rescan", "no"], capture_output=True, text=True, timeout=4)
                        for line in r.stdout.splitlines():
                            if line.startswith("*:") and line[2:].isdigit():
                                c["signal"] = int(line[2:])
            except (OSError, subprocess.TimeoutExpired):
                pass
        for c in out:
            h = hs.get(c["iface"])
            con = c.pop("hotspot_con", "")
            if not h and con:
                try:
                    r = subprocess.run(["nmcli", "-t", "-g", "802-11-wireless.ssid,802-11-wireless.band,802-11-wireless.channel", "connection", "show", con],
                                       capture_output=True, text=True, timeout=4)
                    v = (r.stdout.splitlines() + ["", "", ""])[:3]
                    ch = int(v[2]) if v[2].isdigit() else 0
                    h = {"ssid": v[0], "band": v[1] if v[1] in ("a", "bg") else "bg", "channel": ch}
                except (OSError, subprocess.TimeoutExpired):
                    h = {"ssid": "", "band": "bg", "channel": 0}
            c["hotspot"] = ({"ssid": str(h.get("ssid", "")), "band": h.get("band", "bg"), "channel": h.get("channel", 0),
                             "running": bool(c.pop("hotspot_running", False))} if h else None)
            c.pop("hotspot_running", None)
        return out

    def _name(self, card):
        card["key"] = DeviceNames.key(card.get("usb_id", ""), card["iface"])
        default = dji.device_label(card.get("name", ""), card.get("vendor", ""), card.get("usb_id", ""))
        card["label"] = self.names.label(card["key"], default) if self.names else default
        card["custom"] = bool(self.names and card["label"] != default)

    SYS_NET = "/sys/class/net"

    def labels(self):
        if self.demo:
            return {"wlan0": dji.device_label("802.11ac NIC", "Realtek", "0bda:c811"), "wlan1": dji.device_label("802.11ac NIC", "Realtek", "2357:011e")}

        def raw():
            out = []
            try:
                names = sorted(os.listdir(self.SYS_NET))
            except OSError:
                return out
            for n in names:
                if os.path.isdir(f"{self.SYS_NET}/{n}/wireless") and not n.startswith("p2p"):
                    info = dji.netdev_info(n, self.SYS_NET)
                    out.append((n, info["usb_id"], dji.device_label(info["name"], info["vendor"], info["usb_id"])))
            return out
        res = {}
        for n, usb_id, default in ttl_cached("wifi_labels", 10.0, raw) or []:
            res[n] = (self.names.label(DeviceNames.key(usb_id, n), default) if self.names else default) or ""
        return {n: v for n, v in res.items() if v}

    def driver_status(self):
        try:
            with open(self.DRIVER_STATUS) as f:
                raw = json.load(f)
            return {"state": str(raw.get("state", "")), "message": str(raw.get("message", ""))[:300]}
        except (OSError, ValueError, AttributeError):
            return {}

    def status(self):
        if self.demo:
            cards = []
            for iface, ip, ssid, usb in (("wlan0", "10.0.0.5", "Demo-Hotspot", "0bda:c811"), ("wlan1", "", "", "2357:011e")):
                c = {"iface": iface, "ip": ip, "camera_net": False, "up": bool(ip), "ssid": ssid, "signal": 80 if ip else None,
                     "name": "802.11ac NIC", "vendor": "Realtek", "usb_id": usb, "driver": "rtl8821cu", "ap": True, "band24": True, "band5": True}
                h = self.fake_hs.get(iface)
                if h and h.get("running", True):
                    c.update(ip="10.42.0.1", ssid="", signal=None, up=True)
                c["hotspot"] = {"ssid": h["ssid"], "band": h["band"], "channel": h["channel"], "running": h.get("running", True)} if h else None
                self._name(c)
                cards.append(c)
            return {"helper_installed": True, "cards": cards,
                    "state": "done" if self.fake_msg[0] else "idle", "message": self.fake_msg[1], "action": self.fake_msg[0], "scan": {"iface": "wlan0", "nets": [
                        {"ssid": "Demo-Hotspot", "signal": 80, "security": "WPA2", "in_use": False},
                        {"ssid": "Mein Handy", "signal": 62, "security": "WPA2 WPA3", "in_use": False},
                        {"ssid": "Gast", "signal": 31, "security": "offen", "in_use": False}]}, "saved": []}
        try:
            with open(self.STATUS) as f:
                h = json.load(f)
        except (OSError, ValueError):
            h = {}
        return {"helper_installed": os.path.exists("/etc/systemd/system/pipbox-wifi.path"), "cards": self.cards(),
                "state": h.get("state", "idle"), "message": h.get("message", ""), "action": h.get("action", ""), "scan": h.get("scan") or {},
                "saved": h.get("saved") or [], "time": h.get("time", 0), "driver": self.driver_status()}

    def _hotspot_request(self, d, st, card):
        action, iface = d.get("action"), card["iface"]
        req = {"action": action, "iface": iface}
        running = bool((card.get("hotspot") or {}).get("running"))
        saved = self.hotspots().get(iface) or {}
        if action == "hotspot_stop":
            if not (card.get("hotspot") or {}):
                raise ValueError("Auf dieser Karte ist kein Hotspot eingerichtet")
            return req
        if action == "hotspot_delete":
            if not (card.get("hotspot") or {}):
                raise ValueError("Auf dieser Karte ist kein Hotspot eingerichtet")
            if running:
                raise ValueError("Der Hotspot läuft: bitte zuerst ausschalten, dann löschen")
            return req
        if action == "hotspot_save" and running:
            raise ValueError("Der Hotspot läuft: bitte zuerst ausschalten, dann einstellen")
        if action == "hotspot_start" and d.get("ssid") is None:
            if not saved.get("ssid") or not saved.get("password"):
                raise ValueError("Bitte zuerst „Einstellen“: Name und Passwort des Hotspots festlegen")
            d = dict(d, ssid=saved["ssid"], password="", band=saved.get("band", "bg"), channel=saved.get("channel", 0))
        ssid = d.get("ssid")
        if (not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or ssid != ssid.strip()
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in ssid) or ssid.startswith(self.HS_PREFIX)):
            raise ValueError("Name des Hotspots: 1 bis 32 Zeichen, keine Leerzeichen am Anfang oder Ende")
        pw = d.get("password", "")
        if not isinstance(pw, str):
            raise ValueError("Passwort ungültig")
        if pw == "":
            if not saved.get("password"):
                raise ValueError("Bitte ein Passwort vergeben (8 bis 63 Zeichen)")
        elif not 8 <= len(pw) <= 63 or any(not 32 <= ord(ch) < 127 for ch in pw):
            raise ValueError("Passwort: 8 bis 63 Zeichen, nur Buchstaben, Ziffern und Satzzeichen ohne Umlaute")
        band = d.get("band", "bg")
        if band not in self.HS_BANDS:
            raise ValueError("Band: 2,4 GHz oder 5 GHz")
        ch = d.get("channel", 0)
        if isinstance(ch, bool) or not isinstance(ch, int) or (ch != 0 and ch not in self.HS_BANDS[band]):
            raise ValueError("Kanal passt nicht zum Band")
        if card.get("ap") is False:
            raise ValueError("Diese WLAN-Karte kann keinen Hotspot aufbauen")
        if band == "a" and card.get("band5") is False:
            raise ValueError("Diese WLAN-Karte kann kein 5 GHz")
        if action == "hotspot_start" and card.get("ip") and not running and d.get("confirm") is not True:
            raise ValueError("Bestätigung fehlt: Die Karte ist gerade mit einem WLAN verbunden, diese Verbindung wird beendet")
        req.update(ssid=ssid, password=pw, band=band, channel=ch)
        return req

    def request(self, d):
        if not self.lock.acquire(blocking=False):
            raise ValueError("Es läuft schon eine Aktion")
        try:
            return self._request(d)
        finally:
            self.lock.release()

    def _request(self, d):
        action = d.get("action")
        if action not in self.ACTIONS:
            raise ValueError("Unbekannte Aktion")
        st = self.status()
        if not st["helper_installed"]:
            raise ValueError("Der WLAN-Helfer ist nicht installiert (install.sh erneut ausführen)")
        if st["state"] == "working" and time.time() - st.get("time", 0) < 90:
            raise ValueError("Es läuft schon eine Aktion")
        iface = str(d.get("iface", ""))
        card = None
        if action in ("scan", "connect", "disconnect") or action.startswith("hotspot_"):
            card = next((c for c in st["cards"] if c["iface"] == iface), None)
            if not card:
                raise ValueError("Diese WLAN-Karte gibt es nicht")
            if card["camera_net"]:
                raise ValueError("Diese Karte ist das Kameranetz")
            if action in ("scan", "connect", "disconnect") and (card.get("hotspot") or {}).get("running"):
                raise ValueError("Auf dieser Karte läuft ein Hotspot. Bitte zuerst den Hotspot beenden.")
        req = {"action": action, "iface": iface}
        if action.startswith("hotspot_"):
            req = self._hotspot_request(d, st, card)
        if action in ("connect", "forget"):
            ssid = d.get("ssid")
            if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
                raise ValueError("Netzname fehlt oder ist zu lang")
            if ssid.startswith(self.HS_PREFIX):
                raise ValueError("Dieser Netzname ist für den Hotspot der Box reserviert")
            req["ssid"] = ssid
        if action == "connect":
            pw = d.get("password", "")
            if not isinstance(pw, str) or len(pw) > 64:
                raise ValueError("Passwort ungültig")
            req.update(password=pw, hidden=d.get("hidden") is True)
        if action == "hotspot_start":
            self._leave_uplinks(iface)
        if self.demo:
            if action in ("hotspot_start", "hotspot_save"):
                run = action == "hotspot_start"
                self.fake_hs[iface] = {"ssid": req["ssid"], "password": req["password"] or (self.fake_hs.get(iface) or {}).get("password", ""),
                                       "band": req["band"], "channel": req["channel"], "running": run}
                self.fake_msg = (action, ("Hotspot „%s“ läuft auf %s (Vorschau)" % (req["ssid"], iface)) if run else "Hotspot-Einstellungen gespeichert (Vorschau)")
            elif action == "hotspot_stop" and iface in self.fake_hs:
                self.fake_hs[iface]["running"] = False
                self.fake_msg = (action, "Hotspot beendet (Vorschau)")
            elif action == "hotspot_delete":
                self.fake_hs.pop(iface, None)
                self.fake_msg = (action, "Hotspot gelöscht (Vorschau)")
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(req, f)

    EXPORT_MAX = 262144

    def helper_call(self, req, wait=40):
        if not self.lock.acquire(timeout=10):
            raise ValueError("Es läuft schon eine WLAN-Aktion")
        try:
            return self._helper_call(req, wait)
        finally:
            self.lock.release()

    def _helper_call(self, req, wait=40):
        st = self.status()
        if not st["helper_installed"]:
            raise RuntimeError("Der WLAN-Helfer ist nicht installiert (install.sh erneut ausführen)")
        if st["state"] == "working" and time.time() - st.get("time", 0) < 90:
            raise ValueError("Es läuft schon eine WLAN-Aktion")
        mark = secrets.token_hex(8)
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(dict(req, mark=mark), f)
        end = time.monotonic() + wait
        while time.monotonic() < end:
            time.sleep(0.4)
            try:
                with open(self.STATUS) as f:
                    h = json.load(f)
            except (OSError, ValueError):
                continue
            if h.get("mark") == mark and h.get("state") in ("done", "error"):
                return h
        raise RuntimeError("Der WLAN-Helfer hat nicht rechtzeitig geantwortet")

    def export_saved(self, with_passwords):
        if self.demo:
            return {"networks": [{"ssid": "Demo-Hotspot", "hidden": False, "open": False, "password": "demo-passwort-1" if with_passwords else ""},
                                 {"ssid": "Gast", "hidden": False, "open": True, "password": ""}], "skipped": []}
        h = self.helper_call({"action": "export_wifi", "secrets": bool(with_passwords)})
        path = os.path.join(os.path.dirname(self.req), "wifi-export.json")
        if h.get("state") == "error":
            try:
                os.unlink(path)
            except OSError:
                pass
            raise RuntimeError(h.get("message") or "Der WLAN-Helfer meldet einen Fehler")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("keine normale Datei")
                raw = os.read(fd, self.EXPORT_MAX + 1)
            finally:
                os.close(fd)
            os.unlink(path)
        except OSError:
            raise RuntimeError(h.get("message") or "Der WLAN-Helfer hat keine Liste geliefert")
        try:
            d = json.loads(raw.decode("utf-8")) if len(raw) <= self.EXPORT_MAX else None
        except ValueError:
            d = None
        if not isinstance(d, dict) or not isinstance(d.get("networks"), list):
            raise RuntimeError("Die Liste des WLAN-Helfers ist ungültig")
        return {"networks": [n for n in d["networks"] if isinstance(n, dict)], "skipped": [x for x in d.get("skipped", []) if isinstance(x, dict)]}

    def import_saved(self, networks):
        if not networks:
            return "Keine WLAN-Netze zum Einspielen"
        if self.demo:
            return "%d WLAN-Netze eingespielt (Vorschau)" % len(networks)
        h = self.helper_call({"action": "import_wifi", "networks": networks})
        if h.get("state") == "error":
            raise RuntimeError(h.get("message") or "Der WLAN-Helfer meldet einen Fehler")
        return h.get("message") or "WLAN-Netze eingespielt"

    def hotspot_import(self, entries):
        cur = self.hotspots()
        out, skipped = dict(cur), []
        for iface, h in entries.items():
            pw = h.get("password") or (cur.get(iface) or {}).get("password", "")
            if not pw:
                skipped.append(iface)
                continue
            out[iface] = {"ssid": h["ssid"], "password": pw, "band": h["band"], "channel": h["channel"]}
        if self.demo:
            for iface, h in out.items():
                self.fake_hs[iface] = dict(h, running=(self.fake_hs.get(iface) or {}).get("running", False))
        else:
            tmp = self.hs_file + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(out, f)
            os.replace(tmp, self.hs_file)
        n = len(entries) - len(skipped)
        return "%d Hotspots eingespielt" % n + ((", ohne Passwort ausgelassen: " + ", ".join(skipped)) if skipped else "")

    def _leave_uplinks(self, iface):
        if self.srtla is None:
            return
        ups = list(((self.srtla.data or {}).get("settings") or {}).get("uplinks") or [])
        if iface not in ups:
            return
        rest = [u for u in ups if u != iface]
        valid = [o["iface"] for o in iface_ips()]
        if not any(u in valid for u in rest):
            raise ValueError("Diese Karte ist gerade das einzige Netz zum Senden. Bitte zuerst ein anderes Netz zum Senden wählen.")
        self.srtla.set_settings({"uplinks": rest}, valid or ["eth0", "eth1"])
