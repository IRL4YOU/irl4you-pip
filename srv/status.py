
import json
import os
import time

from srv.netinfo import _alert_text, iface_ips, lan_ip
from srv.settings import SettingsTransfer


class UplinkMemory:

    def __init__(self):
        self.up = set()

    def reset(self):
        self.up = set()

    def dropped(self, ups):
        self.up &= set(ups)
        for n, v in ups.items():
            if (v or {}).get("state") in ("an", "reserve"):
                self.up.add(n)
        return sorted(n for n in self.up if (ups.get(n) or {}).get("state") == "aus")


class FpsMemory:
    FPS_LOW = 20.0
    FPS_LOW_HOLD_S = 15.0
    GAP_S = 7.0

    def __init__(self):
        self.since = {}

    def reset(self):
        self.since = {}

    def low(self, rates, now=None):
        now = time.time() if now is None else now
        out = {}
        for key in list(self.since):
            if key not in rates:
                del self.since[key]
        for key, (dmx, dec) in rates.items():
            if dmx < self.FPS_LOW:
                kind, fps = "camera", dmx
            elif 0 < dec < self.FPS_LOW:
                kind, fps = "box", dec
            else:
                self.since.pop(key, None)
                continue
            first, last, was = self.since.get(key, (now, now, kind))
            if now - last > self.GAP_S or was != kind:
                first = now
            self.since[key] = (first, now, kind)
            if now - first >= self.FPS_LOW_HOLD_S:
                out[key] = (kind, fps)
        return out


def status_light(m, live, limits=None, battery_limit=10, memory=None, fps_memory=None):
    reasons = []

    def add(level, text, kind=None):
        reasons.append({"level": level, "text": text} if kind is None else {"level": level, "text": text, "kind": kind})
    alerts = [a for a in (m.get("alerts") or []) if isinstance(a, dict)]
    for a in alerts:
        add("crit" if a.get("level") == "crit" else "warn", _alert_text(a))
    seen = " ".join(r["text"] for r in reasons)
    cpu, mem = (m.get("cpu") or {}).get("avg"), (m.get("mem") or {}).get("used_pct")
    for key, label, unit, val, word in (("temp", "Temperatur", "°C", m.get("temp_c"), "Temperatur"), ("cpu", "CPU-Last", "%", cpu, "CPU"), ("ram", "RAM", "%", mem, "RAM")):
        lim = (limits or {}).get(key) or {}
        if lim.get("on") and isinstance(val, (int, float)) and not isinstance(val, bool) and isinstance(lim.get("limit"), int) and val >= lim["limit"] and word not in seen:
            add("warn", "%s %d %s (Grenze %d %s)" % (label, round(val), unit, lim["limit"], unit))
    for c in m.get("cameras") or []:
        b = c.get("battery")
        if isinstance(b, int) and not isinstance(b, bool) and b <= battery_limit and c.get("charging") is not True and not c.get("parked") and (c.get("battery_age") is None or c["battery_age"] <= 300):
            add("warn", "Akku %s %d %%" % (c.get("name") or c.get("key") or "Kamera", b))
    if fps_memory is not None:
        if (limits or {}).get("fps_on") is False or not live:
            fps_memory.reset()
        else:
            cams = {c.get("key"): c for c in m.get("cameras") or []}
            rates = {c["key"]: tuple(c["rates"]) for c in cams.values() if c.get("key") and isinstance(c.get("rates"), (list, tuple)) and len(c["rates"]) == 2}
            for key, (kind, fps) in sorted(fps_memory.low(rates).items()):
                name = cams[key].get("name") or key
                add("warn", "%s: nur %d fps" % (name, round(fps)), "fps")
    ups = m.get("uplinks") or {}
    if memory is not None:
        if not live:
            memory.reset()
        elif ups:
            names = m.get("conn_names") or {}
            down = memory.dropped(ups)
            if down:
                if not any((v or {}).get("state") in ("an", "reserve") for v in ups.values()):
                    add("crit", "Kein Sendeweg mehr verbunden")
                else:
                    add("warn", "Sendeweg ausgefallen: " + ", ".join(str(names.get(n) or n) for n in down))
    level = "crit" if any(r["level"] == "crit" for r in reasons) else ("warn" if reasons else "ok")
    return {"level": level, "live": bool(live), "reasons": reasons[:10]}


class NetChoice:

    def __init__(self, path):
        self.path = path
        try:
            with open(path) as f:
                self.iface = json.load(f).get("iface")
        except (OSError, ValueError):
            self.iface = None

    def options(self):
        return iface_ips()

    def ip(self):
        for o in self.options():
            if o["iface"] == self.iface:
                return o.get("cam_ip") or o["ip"]
        return lan_ip()

    def select(self, iface):
        if iface not in [o["iface"] for o in self.options()]:
            raise ValueError("Unbekannte Schnittstelle oder keine IPv4-Adresse")
        self.iface = iface
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"iface": iface}, f)
        os.replace(tmp, self.path)

    def status(self):
        return {"options": self.options(), "selected": self.iface, "ip": self.ip()}


class IfaceMemory:

    def __init__(self, path):
        self.path = path

    def _last(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, cur):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cur, f)
        os.replace(tmp, self.path)

    def reconcile(self, srtla, netchoice, cams, wifi=None, names=None):
        cur = SettingsTransfer.local_macs()
        last = self._last()
        by_mac = {m: n for n, m in cur.items()}
        mapping = {name: by_mac[mac] for name, mac in last.items() if cur.get(name) != mac and by_mac.get(mac) not in (None, name)}
        if mapping:
            self._apply(mapping, srtla, netchoice, cams, wifi, names)
            print("iface_macs:", ", ".join("%s->%s" % kv for kv in sorted(mapping.items())), flush=True)
        if cur != last:
            try:
                self._save(cur)
            except OSError:
                pass
        return mapping

    @staticmethod
    def _apply(m, srtla, netchoice, cams, wifi=None, names=None):
        r = lambda n: m.get(n, n)
        with srtla.lock:
            st = srtla.data.get("settings") or {}
            if isinstance(st.get("uplinks"), list):
                st["uplinks"] = sorted(set(r(u) for u in st["uplinks"]))
            if isinstance(st.get("min_share"), dict):
                st["min_share"] = {r(k): v for k, v in st["min_share"].items()}
            srtla.save()
        if netchoice is not None and netchoice.iface in m:
            netchoice.iface = r(netchoice.iface)
            tmp = netchoice.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"iface": netchoice.iface}, f)
            os.replace(tmp, netchoice.path)
        with cams.lock:
            changed = False
            for c in cams.cams:
                if c.get("iface") in m:
                    c["iface"] = r(c["iface"])
                    changed = True
            if changed:
                cams.save()
        if wifi is not None and not getattr(wifi, "demo", False):
            hs = wifi.hotspots()
            if any(k in m for k in hs):
                out = {r(k): v for k, v in hs.items()}
                tmp = wifi.hs_file + ".tmp"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump(out, f)
                os.replace(tmp, wifi.hs_file)
        if names is not None:
            with names.lock:
                d = names._all()
                out = {}
                for k, v in d.items():
                    pre = k[:3] if k.startswith("if:") else (k[:4] if k.startswith("net:") else "")
                    out[pre + r(k[len(pre):]) if pre else k] = v
                if out != d:
                    tmp = names.path + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(out, f)
                    os.replace(tmp, names.path)

    def run(self, srtla, netchoice, cams, wifi=None, names=None, every=30):
        while True:
            try:
                self.reconcile(srtla, netchoice, cams, wifi, names)
            except Exception as e:
                print("iface_macs:", type(e).__name__, flush=True)
            time.sleep(every)
