
import ipaddress
import json
import os
import re
import secrets
import subprocess
import time
import hdmi_daemon
from tw.store import TwitchStore

from srv.cameras import KEY_RE, ROLES
from srv.dji_service import DjiService
from srv.hdmi_service import HdmiService
from srv.netinfo import IFACE_NAME_RE, iface_ips
from srv.pipeline import PipelineStore
from srv.srtla import SrtlaStore
from srv.util import BASE_DIR, read
from srv.vault import Vault
from srv.wifi import DeviceNames, Wifi


SETTINGS_FORMAT = "irl4you-box-einstellungen"


SETTINGS_VERSION = 1


SETTINGS_SECTIONS = (("cameras", "Kameras"), ("pipeline", "Bildaufbau"), ("srtla", "SRTLA-Server und Sendeeinstellungen"),
                     ("autostart", "Automatischer Start"), ("names", "Namen (Verbindungen, WLAN- und Bluetooth-Sticks)"),
                     ("dji", "DJI-Kameras (Einstellungen)"), ("hdmi", "HDMI-Eingang (Einstellungen)"), ("twitch", "Akku-Warnung im Twitch-Chat (Einstellungen)"),
                     ("camnet", "Netzwerk für Kameras (Standard)"),
                     ("hotspots", "Hotspots"), ("wifi", "Gespeicherte WLAN-Netze"))


class SettingsTransfer:
    MAX_BODY = 262144

    def __init__(self, state_dir, cams, pipeline, srtla, autostart, names, djisvc, wifi, send, demo=False):
        self.cams, self.pipeline, self.srtla, self.autostart = cams, pipeline, srtla, autostart
        self.names, self.djisvc, self.wifi, self.send, self.demo = names, djisvc, wifi, send, demo
        self.progress = {"state": "idle"}
        self.iface_map = {}
        self.hdmi = self.twitch = self.netchoice = None
        self.backup_dir = os.path.join(state_dir, "backup")
        self.backup_path = os.path.join(self.backup_dir, "vor-einspielen.json")
        self.version = (read(os.path.join(BASE_DIR, "VERSION"), "") or "").strip()

    def make_document(self, secrets_on, wifi_data=None):
        doc = {"format": SETTINGS_FORMAT, "version": SETTINGS_VERSION, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "box_version": self.version, "secrets": bool(secrets_on)}
        with self.cams.lock:
            doc["cameras"] = [dict({"name": c["name"], "key": c["key"], "role": c["role"]}, **({"iface": c["iface"]} if c.get("iface") else {}))
                              for c in self.cams.cams]
        doc["pipeline"] = {k: v for k, v in self.pipeline.cfg.items()}
        with self.srtla.lock:
            data = self.srtla.data
            servers = [dict({"name": s["name"], "host": s["host"], "port": s["port"]}, **({"streamid": s.get("streamid", "")} if secrets_on else {}))
                       for s in data["servers"]]
            sel = next((i for i, s in enumerate(data["servers"]) if s["id"] == data.get("selected")), None)
            doc["srtla"] = {"servers": servers, "selected": sel, "settings": dict(data["settings"])}
        doc["autostart"] = {"enabled": bool(self.autostart.enabled())}
        doc["names"] = self.names._all()
        dj = []
        for addr, c in sorted(self.djisvc._config().items()):
            if isinstance(c, dict):
                e = {k: c.get(k) for k in ("name", "model", "kind", "wifi_ifname", "ip", "resolution", "fps", "bitrate", "stabilization", "autoconnect", "ssid", "status_only")
                     if c.get(k) is not None}
                e["addr"] = str(addr).upper()
                if secrets_on and c.get("password"):
                    e["password"] = c["password"]
                dj.append(e)
        doc["dji"] = dj
        hs = {}
        for iface, h in sorted(self.wifi.hotspots().items()):
            e = {k: h.get(k) for k in ("ssid", "band", "channel") if h.get(k) is not None}
            if secrets_on and h.get("password"):
                e["password"] = h["password"]
            hs[iface] = e
        doc["hotspots"] = hs
        doc["ifaces"] = self.local_macs()
        if self.hdmi is not None:
            try:
                st = self.hdmi.status()
            except Exception:
                st = {}
            if st.get("service"):
                doc["hdmi"] = {k: st["settings"][k] for k in HdmiService.ALLOWED}
        if self.twitch is not None:
            with self.twitch.lock:
                t = self.twitch.data
                doc["twitch"] = {"enabled": bool(t["enabled"]), "channel": t["channel"], "login": t["login"], "threshold": t["threshold"],
                                 "message": t["message"], "only_live": bool(t["only_live"])}
        if self.netchoice is not None and self.netchoice.iface:
            doc["camnet"] = {"iface": self.netchoice.iface}
        if wifi_data is not None:
            doc["wifi"] = {"networks": [{k: v for k, v in n.items() if secrets_on or k != "password"} for n in wifi_data.get("networks", [])],
                           "skipped": list(wifi_data.get("skipped", []))}
        return doc

    def export(self, secrets_on, password=None, with_wifi=True):
        if secrets_on and not password:
            raise ValueError("Eine Datei mit Passwörtern wird immer verschlüsselt: bitte ein Passwort zum Verschlüsseln eingeben")
        if password:
            Vault.check_password(password)
        notes, wifi_data = [], None
        if with_wifi:
            try:
                wifi_data = self.wifi.export_saved(secrets_on)
            except (ValueError, RuntimeError) as e:
                notes.append("Die gespeicherten WLAN-Netze sind nicht dabei: " + str(e))
        doc = self.make_document(secrets_on, wifi_data)
        if wifi_data and wifi_data.get("skipped"):
            notes.append("Nicht übertragbar: " + ", ".join("„%s“ (%s)" % (s.get("ssid", "?"), s.get("why", "?")) for s in wifi_data["skipped"][:10]))
        header = {k: doc[k] for k in ("format", "version", "created", "box_version", "secrets")}
        if password:
            out = Vault.seal(json.dumps(doc, ensure_ascii=False).encode("utf-8"), password, header)
        else:
            out = doc
        return {"document": out, "encrypted": bool(password), "notes": notes}

    def read_document(self, raw, password=None):
        if not isinstance(raw, dict) or raw.get("format") != SETTINGS_FORMAT:
            raise ValueError("Das ist keine Sicherung der IRL4YOU BOX")
        ver = raw.get("version")
        if isinstance(ver, bool) or not isinstance(ver, int) or ver < 1:
            raise ValueError("Die Version der Sicherung ist ungültig")
        if ver > SETTINGS_VERSION:
            raise ValueError("Die Sicherung stammt von einer neueren Version (%s). Bitte zuerst die Box aktualisieren." % ver)
        if len(json.dumps(raw)) > self.MAX_BODY:
            raise ValueError("Die Datei ist zu groß")
        if raw.get("encrypted") is True:
            if not password:
                raise ValueError("Die Datei ist verschlüsselt: bitte das Passwort eingeben")
            try:
                doc = json.loads(Vault.open(raw, password).decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                if isinstance(e, ValueError) and ("Passwort" in str(e) or "Datei" in str(e)):
                    raise
                raise ValueError("Der Inhalt der Sicherung ist beschädigt")
            if not isinstance(doc, dict) or doc.get("format") != SETTINGS_FORMAT or doc.get("version") != ver:
                raise ValueError("Der Inhalt der Sicherung passt nicht zur Kennung")
            return doc, True
        if raw.get("secrets") is True:
            raise ValueError("Eine Sicherung mit Passwörtern muss verschlüsselt sein")
        return raw, False

    @staticmethod
    def _text(v, lo, hi, what):
        if not isinstance(v, str) or not lo <= len(v.strip()) <= hi or any(not ch.isprintable() for ch in v):
            raise ValueError("%s ungültig" % what)
        return v.strip()

    def _clean_cameras(self, raw):
        if not isinstance(raw, list) or len(raw) > 40:
            raise ValueError("Die Kameraliste ist ungültig (höchstens 40 Kameras)")
        out, keys, roles, names, notes = [], set(), set(), set(), []
        for c in raw:
            if not isinstance(c, dict):
                raise ValueError("Die Kameraliste ist ungültig")
            name = self._text(c.get("name"), 1, 40, "Kameraname")
            key = c.get("key")
            if not isinstance(key, str) or not KEY_RE.match(key):
                raise ValueError("Ein Kamera-Schlüssel ist ungültig")
            if key in keys:
                raise ValueError("Der Kamera-Schlüssel „%s“ kommt doppelt vor" % key)
            role = c.get("role") if c.get("role") in ROLES else "extra"
            if role in ("main", "pip") and role in roles:
                role = "extra"
                notes.append("Rolle von „%s“ auf „extra“ gestellt (die Rolle war doppelt)" % name)
            if name.casefold() in names:
                name = (name[:34] + " " + key[-5:]).strip()
            iface = c.get("iface") if isinstance(c.get("iface"), str) and IFACE_NAME_RE.match(c.get("iface", "")) else ""
            keys.add(key)
            roles.add(role)
            names.add(name.casefold())
            out.append({"name": name, "key": key, "role": role, "iface": iface})
        return out, notes

    def _clean_pipeline(self, raw, keys):
        if not isinstance(raw, dict):
            raise ValueError("Der Bildaufbau ist ungültig")
        tmp = PipelineStore(os.devnull)
        tmp.save = lambda: None
        tmp.set(dict(raw), list(keys))
        return dict(raw), []

    def _clean_srtla(self, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("servers"), list) or len(raw["servers"]) > 30:
            raise ValueError("Die SRTLA-Server sind ungültig (höchstens 30)")
        servers, notes = [], []
        for s in raw["servers"]:
            if not isinstance(s, dict):
                raise ValueError("Die SRTLA-Server sind ungültig")
            servers.append(SrtlaStore.check({"name": s.get("name"), "host": s.get("host"), "port": s.get("port"), "streamid": s.get("streamid") or ""}))
        sel = raw.get("selected")
        if isinstance(sel, bool) or not (sel is None or isinstance(sel, int)) or (isinstance(sel, int) and not 0 <= sel < len(servers)):
            sel = 0 if servers else None
        st = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
        cur = SrtlaStore.DEFAULT_SETTINGS
        try:
            mn, mx, lat = int(st.get("min_kbps", cur["min_kbps"])), int(st.get("max_kbps", cur["max_kbps"])), int(st.get("latency_ms", cur["latency_ms"]))
        except (TypeError, ValueError):
            raise ValueError("Bitrate und Latenz sind ungültig")
        if not 100 <= mn < mx <= 20000 or not 100 <= lat <= 10000:
            raise ValueError("Bitrate oder Latenz außerhalb des erlaubten Bereichs")
        spread = st.get("spread", "best")
        if spread not in ("best", "all"):
            raise ValueError("Verteilung ungültig")
        ups = st.get("uplinks", [])
        if not isinstance(ups, list) or len(ups) > 20 or any(not isinstance(u, str) or not IFACE_NAME_RE.match(u) for u in ups):
            raise ValueError("Die Netze zum Senden sind ungültig")
        return {"servers": servers, "selected": sel, "settings": {"min_kbps": mn, "max_kbps": mx, "latency_ms": lat, "spread": spread,
                                                                  "uplinks": sorted(set(ups))}}, notes

    def _clean_autostart(self, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("enabled"), bool):
            raise ValueError("Der automatische Start ist ungültig")
        return {"enabled": raw["enabled"]}, []

    def _clean_names(self, raw):
        if not isinstance(raw, dict) or len(raw) > 60:
            raise ValueError("Die Namen der Sticks sind ungültig")
        out = {}
        for k, v in raw.items():
            if not isinstance(k, str) or not DeviceNames.KEY_RE.match(k) or not isinstance(v, str):
                raise ValueError("Die Namen der Sticks sind ungültig")
            v = " ".join(v.split())
            if len(v) > 40 or any(not ch.isprintable() for ch in v):
                raise ValueError("Ein Name eines Sticks ist ungültig")
            if v:
                out[k] = v
        return out, []

    def _clean_dji(self, raw):
        if not isinstance(raw, list) or len(raw) > 20:
            raise ValueError("Die DJI-Kameras sind ungültig (höchstens 20)")
        out, seen = [], set()
        for c in raw:
            if not isinstance(c, dict) or not isinstance(c.get("addr"), str) or not DjiService.MAC_RE.match(c["addr"]):
                raise ValueError("Eine DJI-Kamera hat keine gültige Geräteadresse")
            addr = c["addr"].upper()
            if addr in seen:
                raise ValueError("Eine DJI-Kamera kommt doppelt vor")
            seen.add(addr)
            e = {"addr": addr, "name": self._text(c.get("name") or c.get("model") or addr, 1, 40, "Kameraname"),
                 "model": self._text(c.get("model") or "", 0, 60, "Modell"), "kind": c.get("kind") if isinstance(c.get("kind"), str) and re.fullmatch(r"[a-z0-9_]{1,20}", c["kind"]) else "unknown"}
            wi = c.get("wifi_ifname", "")
            if wi not in ("", "manual") and not (isinstance(wi, str) and IFACE_NAME_RE.match(wi)):
                raise ValueError("Die Verbindung einer DJI-Kamera ist ungültig")
            e["wifi_ifname"] = wi
            ip = c.get("ip", "")
            if ip:
                try:
                    ipaddress.IPv4Address(ip)
                except (ValueError, TypeError):
                    raise ValueError("Die Adresse einer DJI-Kamera ist ungültig")
            e["ip"] = ip or ""
            ssid = c.get("ssid", "")
            if not isinstance(ssid, str) or len(ssid.encode("utf-8")) > 32 or any(ord(ch) < 32 or ord(ch) == 127 for ch in ssid):
                raise ValueError("Das WLAN einer DJI-Kamera ist ungültig")
            e["ssid"] = ssid
            pw = c.get("password", "")
            if not isinstance(pw, str) or len(pw) > 64 or any(ord(ch) < 32 or ord(ch) == 127 for ch in pw):
                raise ValueError("Das WLAN-Passwort einer DJI-Kamera ist ungültig")
            if pw:
                e["password"] = pw
            for k, lo, hi in (("fps", 1, 120), ("bitrate", 100, 40000)):
                v = c.get(k)
                if v is not None:
                    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                        raise ValueError("Ein Wert einer DJI-Kamera ist ungültig (%s)" % k)
                    e[k] = v
            for k in ("resolution", "stabilization"):
                v = c.get(k)
                if v is not None:
                    if not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_+.-]{1,20}", v):
                        raise ValueError("Ein Wert einer DJI-Kamera ist ungültig (%s)" % k)
                    e[k] = v
            if c.get("autoconnect") is not None:
                if not isinstance(c["autoconnect"], bool):
                    raise ValueError("Ein Wert einer DJI-Kamera ist ungültig (autoconnect)")
                e["autoconnect"] = c["autoconnect"]
            if c.get("status_only") is not None:
                if not isinstance(c["status_only"], bool):
                    raise ValueError("Ein Wert einer DJI-Kamera ist ungültig (status_only)")
                e["status_only"] = c["status_only"]
            out.append(e)
        return out, ["Die Bluetooth-Kopplung lässt sich nicht mitnehmen: Die Kameras müssen nach dem Einspielen einmal neu verbunden werden"] if out else []

    def _clean_hdmi(self, raw):
        if not isinstance(raw, dict):
            raise ValueError("Die HDMI-Einstellungen sind ungültig")
        want = {k: raw[k] for k in HdmiService.ALLOWED if k in raw}
        out = hdmi_daemon.clean_settings(want, None)
        return {k: out[k] for k in HdmiService.ALLOWED}, []

    def _clean_twitch(self, raw):
        if not isinstance(raw, dict):
            raise ValueError("Die Einstellungen der Akku-Warnung sind ungültig")
        for k in ("enabled", "only_live"):
            if not isinstance(raw.get(k), bool):
                raise ValueError("Die Einstellungen der Akku-Warnung sind ungültig")
        out = {"enabled": raw["enabled"], "only_live": raw["only_live"],
               "channel": TwitchStore._name(raw.get("channel", ""), TwitchStore.ERR_CHANNEL), "login": TwitchStore._name(raw.get("login", ""), TwitchStore.ERR_LOGIN),
               "threshold": TwitchStore._threshold(raw.get("threshold")), "message": TwitchStore._message(raw.get("message"))}
        return out, ["Token und Twitch-Anmeldung werden nie mitgenommen: bitte hier neu anmelden"]

    def _clean_camnet(self, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("iface"), str) or not IFACE_NAME_RE.match(raw["iface"]):
            raise ValueError("Das Netzwerk für Kameras ist ungültig")
        return {"iface": raw["iface"]}, []

    def _clean_hotspots(self, raw):
        if not isinstance(raw, dict) or len(raw) > 8:
            raise ValueError("Die Hotspots sind ungültig")
        out, notes = {}, []
        for iface, h in raw.items():
            if not isinstance(iface, str) or not re.fullmatch(r"[a-z][a-z0-9]{1,14}", iface) or not isinstance(h, dict):
                raise ValueError("Die Hotspots sind ungültig")
            ssid, pw, band, ch = h.get("ssid"), h.get("password", ""), h.get("band", "bg"), h.get("channel", 0)
            if (not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or ssid != ssid.strip() or any(ord(c) < 32 or ord(c) == 127 for c in ssid)
                    or ssid.startswith(Wifi.HS_PREFIX)):
                raise ValueError("Der Name eines Hotspots ist ungültig")
            if not isinstance(pw, str) or (pw and (not 8 <= len(pw) <= 63 or any(not 32 <= ord(c) < 127 for c in pw))):
                raise ValueError("Das Passwort eines Hotspots ist ungültig")
            if band not in Wifi.HS_BANDS or isinstance(ch, bool) or not isinstance(ch, int) or (ch != 0 and ch not in Wifi.HS_BANDS[band]):
                raise ValueError("Band oder Kanal eines Hotspots ist ungültig")
            out[iface] = {"ssid": ssid, "password": pw, "band": band, "channel": ch}
        return out, notes

    def _clean_wifi(self, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("networks"), list) or len(raw["networks"]) > 50:
            raise ValueError("Die gespeicherten WLAN-Netze sind ungültig (höchstens 50)")
        out, notes, seen = [], [], set()
        for n in raw["networks"]:
            if not isinstance(n, dict):
                raise ValueError("Die gespeicherten WLAN-Netze sind ungültig")
            ssid, pw = n.get("ssid"), n.get("password", "")
            if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or any(ord(c) < 32 or ord(c) == 127 for c in ssid):
                raise ValueError("Der Name eines WLAN-Netzes ist ungültig")
            if ssid.startswith(Wifi.HS_PREFIX) or ssid in seen:
                continue
            seen.add(ssid)
            if not isinstance(pw, str) or (pw and not (8 <= len(pw) <= 63 and all(32 <= ord(c) < 127 for c in pw) or re.fullmatch(r"[0-9a-fA-F]{64}", pw))):
                raise ValueError("Ein WLAN-Passwort ist ungültig")
            sec = n.get("security", "none" if n.get("open") is True else "wpa-psk")
            if sec not in ("wpa-psk", "sae", "none"):
                raise ValueError("Die Sicherheitsart eines WLAN-Netzes ist ungültig")
            open_net = n.get("open") is True or sec == "none"
            if open_net and pw:
                raise ValueError("Ein offenes WLAN-Netz hat kein Passwort")
            if not pw and not open_net:
                notes.append("„%s“ ohne Passwort gesichert: bitte neu verbinden" % ssid)
                continue
            out.append({"ssid": ssid, "password": pw, "hidden": n.get("hidden") is True, "open": open_net, "security": "none" if open_net else sec})
        return {"networks": out}, notes

    @staticmethod
    def local_macs():
        out = {}
        try:
            for n in sorted(os.listdir("/sys/class/net")):
                if n == "lo" or not IFACE_NAME_RE.match(n):
                    continue
                m = (read("/sys/class/net/%s/address" % n, "") or "").strip().lower()
                if re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", m) and m != "00:00:00:00:00:00":
                    out[n] = m
        except OSError:
            pass
        return out

    def _iface_map(self, doc):
        old = doc.get("ifaces")
        if not isinstance(old, dict):
            return {}
        mine = {mac: name for name, mac in self.local_macs().items()}
        out = {}
        for name, mac in old.items():
            if isinstance(name, str) and IFACE_NAME_RE.match(name) and isinstance(mac, str) and mine.get(mac.lower()) not in (None, name):
                out[name] = mine[mac.lower()]
        return out

    @staticmethod
    def _remap_ifaces(doc, m):
        d = json.loads(json.dumps(doc))
        r = lambda n: m.get(n, n) if isinstance(n, str) else n
        for c in d.get("cameras", []) if isinstance(d.get("cameras"), list) else []:
            if isinstance(c, dict) and c.get("iface"):
                c["iface"] = r(c["iface"])
        if isinstance(d.get("camnet"), dict) and d["camnet"].get("iface"):
            d["camnet"]["iface"] = r(d["camnet"]["iface"])
        st = d.get("srtla", {}).get("settings") if isinstance(d.get("srtla"), dict) else None
        if isinstance(st, dict):
            if isinstance(st.get("uplinks"), list):
                st["uplinks"] = [r(u) for u in st["uplinks"]]
            if isinstance(st.get("min_share"), dict):
                st["min_share"] = {r(k): v for k, v in st["min_share"].items()}
        if isinstance(d.get("hotspots"), dict):
            d["hotspots"] = {r(k): v for k, v in d["hotspots"].items()}
        if isinstance(d.get("dji"), list):
            for e in d["dji"]:
                if isinstance(e, dict) and e.get("wifi_ifname"):
                    e["wifi_ifname"] = r(e["wifi_ifname"])
        if isinstance(d.get("names"), dict):
            d["names"] = {("net:" + r(k[4:]) if isinstance(k, str) and k.startswith("net:") else ("if:" + r(k[3:]) if isinstance(k, str) and k.startswith("if:") else k)): v
                          for k, v in d["names"].items()}
        return d

    def clean(self, doc):
        clean, report, labels = {}, [], dict(SETTINGS_SECTIONS)
        self.iface_map = self._iface_map(doc)
        if self.iface_map:
            doc = self._remap_ifaces(doc, self.iface_map)
        for sid, label in SETTINGS_SECTIONS:
            if sid not in doc:
                continue
            raw = doc[sid]
            try:
                if sid == "cameras":
                    data, notes = self._clean_cameras(raw)
                    count = len(data)
                elif sid == "pipeline":
                    keys = [c["key"] for c in clean["cameras"]] if "cameras" in clean else [c["key"] for c in self.cams.cams]
                    data, notes = self._clean_pipeline(raw, keys)
                    count = 1
                elif sid == "srtla":
                    data, notes = self._clean_srtla(raw)
                    count = len(data["servers"])
                elif sid == "autostart":
                    data, notes = self._clean_autostart(raw)
                    count = 1
                elif sid == "names":
                    data, notes = self._clean_names(raw)
                    count = len(data)
                elif sid == "dji":
                    data, notes = self._clean_dji(raw)
                    count = len(data)
                elif sid == "hdmi":
                    data, notes = self._clean_hdmi(raw)
                    count = 1
                elif sid == "twitch":
                    data, notes = self._clean_twitch(raw)
                    count = 1
                elif sid == "camnet":
                    data, notes = self._clean_camnet(raw)
                    count = 1
                elif sid == "hotspots":
                    data, notes = self._clean_hotspots(raw)
                    count = len(data)
                else:
                    data, notes = self._clean_wifi(raw)
                    count = len(data["networks"])
                clean[sid] = data
                report.append({"id": sid, "label": label, "count": count, "ok": True, "note": "; ".join(notes)})
            except ValueError as e:
                report.append({"id": sid, "label": label, "count": 0, "ok": False, "note": str(e)})
        return clean, report

    def preview(self, raw, password=None):
        doc, encrypted = self.read_document(raw, password)
        _, report = self.clean(doc)
        if not report:
            raise ValueError("In der Datei steht nichts, was sich einspielen lässt")
        return {"ok": True, "encrypted": encrypted, "created": str(doc.get("created", ""))[:32], "box_version": str(doc.get("box_version", ""))[:16],
                "secrets": doc.get("secrets") is True, "sections": report, "has_backup": os.path.isfile(self.backup_path)}

    def _guard(self):
        if self.send._active():
            raise ValueError("Es wird gerade gesendet. Bitte zuerst die Sendung beenden, dann einspielen.")

    def _write_backup(self):
        doc = self.make_document(True)
        os.makedirs(self.backup_dir, mode=0o700, exist_ok=True)
        tmp = self.backup_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f)
        os.replace(tmp, self.backup_path)

    def apply(self, raw, password=None, sections=None):
        self._guard()
        doc, _ = self.read_document(raw, password)
        return self._apply_doc(doc, sections)

    def _apply_doc(self, doc, sections):
        clean, report = self.clean(doc)
        wanted = [s for s, _ in SETTINGS_SECTIONS if s in clean and (sections is None or s in sections)]
        if not wanted:
            raise ValueError("Nichts zum Einspielen gewählt")
        results, labels = [], dict(SETTINGS_SECTIONS)
        total = len(wanted)
        self.progress = {"state": "running", "done": 0, "total": total, "label": ""}
        try:
            self._write_backup()
            for i, sid in enumerate(wanted):
                self.progress = {"state": "running", "done": i, "total": total, "label": labels[sid]}
                try:
                    msg = getattr(self, "_apply_" + sid)(clean[sid])
                    results.append({"id": sid, "label": labels[sid], "ok": True, "message": msg})
                except Exception as e:
                    results.append({"id": sid, "label": labels[sid], "ok": False, "message": str(e) or type(e).__name__})
        finally:
            self.progress = {"state": "done", "done": total, "total": total, "label": ""}
        if self.iface_map:
            results.insert(0, {"id": "ifaces", "label": "Netzwerkkarten", "ok": True,
                               "message": "Namen der Netzwerkkarten anhand der MAC-Adressen angepasst: %s" % ", ".join("%s → %s" % (a, b) for a, b in sorted(self.iface_map.items()))})
        if "cameras" in wanted and "dji" in wanted:
            self._restore_camera_names(clean["cameras"])
        for r in report:
            if not r["ok"] and (sections is None or r["id"] in sections):
                results.append({"id": r["id"], "label": r["label"], "ok": False, "message": "Nicht eingespielt: " + r["note"]})
        return {"ok": True, "results": results, "backup": True}

    def _restore_camera_names(self, cams):
        want = {c["key"]: c["name"] for c in cams}
        with self.cams.lock:
            changed = False
            for c in self.cams.cams:
                n = want.get(c["key"])
                if n and c["name"] != n:
                    c["name"] = n
                    changed = True
            if changed:
                self.cams.save()

    def restore(self):
        self._guard()
        try:
            with open(self.backup_path) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            raise ValueError("Es gibt keinen gesicherten Stand")
        if not isinstance(doc, dict) or doc.get("format") != SETTINGS_FORMAT:
            raise ValueError("Der gesicherte Stand ist beschädigt")
        doc.pop("wifi", None)
        return self._apply_doc(doc, None)

    def _apply_cameras(self, cams):
        with self.cams.lock:
            old = {c["key"]: c for c in self.cams.cams}
            new = []
            for c in cams:
                cam = {"id": old[c["key"]]["id"] if c["key"] in old else secrets.token_hex(4), "name": c["name"], "key": c["key"], "role": c["role"]}
                if c.get("iface") and self.cams.host_for_iface(c["iface"]) is not None:
                    cam["iface"] = c["iface"]
                new.append(cam)
            gone = len([k for k in old if k not in {c["key"] for c in cams}])
            self.cams.cams = new
            self.cams.save()
        return "%d Kameras eingespielt%s" % (len(new), (", %d nicht in der Sicherung entfernt" % gone) if gone else "")

    def _apply_pipeline(self, req):
        self.pipeline.set(dict(req), [c["key"] for c in self.cams.cams])
        return "Bildaufbau eingespielt"

    def _apply_srtla(self, new):
        valid = [o["iface"] for o in iface_ips()]
        with self.srtla.lock:
            old = list(self.srtla.data["servers"])
            servers = []
            for s in new["servers"]:
                same = next((o for o in old if (o["name"], o["host"], o["port"]) == (s["name"], s["host"], s["port"])), None)
                servers.append({"id": same["id"] if same else secrets.token_hex(4), "name": s["name"], "host": s["host"], "port": s["port"],
                                "streamid": s["streamid"] or ((same or {}).get("streamid", ""))})
            self.srtla.data["servers"] = servers
            self.srtla.data["selected"] = servers[new["selected"]]["id"] if servers and new["selected"] is not None else (servers[0]["id"] if servers else None)
            st = dict(new["settings"])
            ups = [u for u in st["uplinks"] if u in valid]
            st["uplinks"] = ups or list(self.srtla.data["settings"].get("uplinks", []))
            self.srtla.data["settings"] = st
            self.srtla.save()
        skipped = len(new["settings"]["uplinks"]) - len(ups)
        return "%d SRTLA-Server eingespielt%s" % (len(servers), (", %d Netze zum Senden gibt es hier nicht (ausgelassen)" % skipped) if skipped else "")

    def _apply_autostart(self, d):
        self.autostart.set_enabled(d["enabled"])
        return "Automatischer Start: " + ("an" if d["enabled"] else "aus")

    def _apply_names(self, names):
        for k, v in names.items():
            self.names.set(k, v)
        return "%d Namen eingespielt" % len(names)

    def _apply_dji(self, cams):
        done, problems = 0, []
        for c in cams:
            try:
                self.djisvc.command({"cmd": "add", "addr": c["addr"], "name": c["name"], "model": c["model"], "kind": c["kind"]})
                upd = {"cmd": "update"}
                upd.update({k: v for k, v in c.items() if k not in ("kind", "model")})
                self.djisvc.command(upd)
                done += 1
            except (ValueError, RuntimeError) as e:
                problems.append("%s: %s" % (c["name"], e))
                if isinstance(e, RuntimeError):
                    break
        if problems and not done:
            raise RuntimeError("; ".join(problems))
        return "%d DJI-Kameras eingespielt (bitte einmal verbinden)" % done + ((", Probleme: " + "; ".join(problems)) if problems else "")

    def _apply_hdmi(self, d):
        if self.hdmi is None:
            raise RuntimeError("Der HDMI-Dienst ist hier nicht verfügbar")
        self.hdmi.set(dict(d))
        return "HDMI-Einstellungen eingespielt"

    def _apply_twitch(self, d):
        if self.twitch is None:
            raise RuntimeError("Die Akku-Warnung ist hier nicht verfügbar")
        self.twitch.set(dict({k: d[k] for k in ("channel", "login", "threshold", "message", "only_live")}, enabled=False))
        if not d["enabled"]:
            return "Akku-Warnung: Einstellungen eingespielt (aus)"
        try:
            self.twitch.set({"enabled": True})
        except ValueError:
            return "Akku-Warnung: eingespielt, bleibt aber ausgeschaltet (Token oder Twitch-Anmeldung fehlt auf dieser Box)"
        return "Akku-Warnung: eingespielt (an)"

    def _apply_camnet(self, d):
        if self.netchoice is None:
            raise RuntimeError("Das Netzwerk für Kameras ist hier nicht verfügbar")
        try:
            self.netchoice.select(d["iface"])
        except ValueError:
            return "Netzwerk für Kameras: „%s“ gibt es hier nicht (ausgelassen)" % d["iface"]
        return "Netzwerk für Kameras eingespielt"

    def _apply_hotspots(self, hs):
        return self.wifi.hotspot_import(hs)

    def _apply_wifi(self, d):
        return self.wifi.import_saved(d["networks"])


def client_wifi_list():
    def run(args):
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=4).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    out = []
    for line in run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"]).splitlines():
        parts = line.replace("\\:", "\x00").split(":")
        if len(parts) < 4 or parts[1] != "wifi" or parts[2] != "connected":
            continue
        conn = parts[3].replace("\x00", ":")
        if run(["nmcli", "-g", "802-11-wireless.mode", "connection", "show", conn]) == "ap":
            continue
        ip = run(["nmcli", "-g", "IP4.ADDRESS", "dev", "show", parts[0]]).split("/")[0].split("\n")[0]
        if ip:
            out.append({"iface": parts[0], "ip": ip})
    return out
