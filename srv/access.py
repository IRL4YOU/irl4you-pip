
import ipaddress
import json
import os
import re
import secrets
import subprocess
import threading
import time

from srv.settings import client_wifi_list
from srv.util import read


class SupportCases:
    KEEP = 1000
    DELETE_WINDOW = 30 * 60.0
    ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    ID_RE = re.compile(r"^IRL-[A-HJKMNP-Z2-9]{6}$")
    TITLE_MAX = 100

    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock
        self.lock = threading.Lock()
        self.cases = []
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, list):
                self.cases = [c for c in saved if isinstance(c, dict) and self.ID_RE.match(str(c.get("id"))) and isinstance(c.get("title"), str)
                              and isinstance(c.get("t"), (int, float))][-self.KEEP:]
        except (OSError, ValueError):
            pass

    @classmethod
    def clean_title(cls, title):
        if not isinstance(title, str):
            raise ValueError("Bitte einen Titel eintragen.")
        t = " ".join("".join(ch if ch.isprintable() else " " for ch in title).split())[:cls.TITLE_MAX]
        if len(t) < 3:
            raise ValueError("Der Titel ist zu kurz (mindestens 3 Zeichen).")
        return t

    def _save(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cases, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def list(self):
        with self.lock:
            return [dict(c, kind="feature" if c.get("kind") == "feature" else "issue") for c in reversed(self.cases)]

    def create(self, title, kind="issue", cid=None):
        if kind not in ("issue", "feature"):
            raise ValueError("Ungültige Anfrage")
        t = self.clean_title(title)
        with self.lock:
            used = {c["id"] for c in self.cases}
            if cid is not None:
                if not isinstance(cid, str) or not self.ID_RE.match(cid) or cid in used:
                    raise ValueError("Ungültige Anfrage")
            else:
                while True:
                    cid = "IRL-" + "".join(secrets.choice(self.ALPHABET) for _ in range(6))
                    if cid not in used:
                        break
            case = {"id": cid, "title": t, "t": int(self.clock()), "done": False, "kind": kind}
            self.cases = (self.cases + [case])[-self.KEEP:]
            self._save()
            return dict(case)

    def delete(self, cid):
        if not isinstance(cid, str) or not self.ID_RE.match(cid):
            raise ValueError("Ungültige Anfrage")
        with self.lock:
            for i, c in enumerate(self.cases):
                if c["id"] == cid:
                    if self.clock() - c["t"] > self.DELETE_WINDOW:
                        raise ValueError("Nur in den ersten 30 Minuten nach dem Anlegen möglich: Danach bleibt der Eintrag erhalten.")
                    del self.cases[i]
                    self._save()
                    return {"ok": True}
        raise KeyError(cid)

    def mark(self, cid, done):
        if not isinstance(cid, str) or not self.ID_RE.match(cid) or not isinstance(done, bool):
            raise ValueError("Ungültige Anfrage")
        with self.lock:
            for c in self.cases:
                if c["id"] == cid:
                    c["done"] = done
                    self._save()
                    return dict(c)
        raise KeyError(cid)


class ExtraAddress:
    CONF = "/etc/pipbox/extra-ip.json"
    STATUS = "/run/pipbox-netaddr/status.json"
    UNIT = "/etc/systemd/system/pipbox-netaddr.path"
    SYS_NET = "/sys/class/net"
    MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
    PRIVATE = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    SKIP = ("lo", "docker", "veth", "br-", "virbr", "p2p", "tailscale", "tun", "tap", "wg", "zt", "wlan", "wl")
    WAIT = 15.0

    def __init__(self, state_dir, demo=False, clock=time.time, sleep=time.sleep):
        self.req = os.path.join(state_dir, "netaddr-request")
        self.demo, self.clock, self.sleep = demo, clock, sleep
        self.fake = {"enabled": False, "mac": "aa:bb:cc:00:11:22", "addr": "", "prefix": 24}

    @classmethod
    def check(cls, mac, addr, prefix):
        import ipaddress
        if not isinstance(mac, str) or not cls.MAC_RE.match(mac):
            raise ValueError("Ungültige MAC-Adresse")
        if not isinstance(addr, str) or isinstance(prefix, bool) or not isinstance(prefix, int):
            raise ValueError("Ungültige Anfrage")
        try:
            a = ipaddress.IPv4Address(addr)
        except ValueError:
            raise ValueError("Ungültige IP-Adresse")
        if not any(a in ipaddress.ip_network(n) for n in cls.PRIVATE):
            raise ValueError("Nur Adressen aus den privaten Bereichen sind erlaubt (10.…, 172.16.… bis 172.31.…, 192.168.…).")
        if not 8 <= prefix <= 30:
            raise ValueError("Die Netzlänge muss zwischen 8 und 30 liegen (üblich: 24).")
        net = ipaddress.ip_network("%s/%d" % (a, prefix), strict=False)
        if a == net.network_address or a == net.broadcast_address:
            raise ValueError("Das ist die Netz- oder Rundrufadresse, nicht die einer Geräteadresse.")
        return str(a)

    def cards(self):
        out = []
        try:
            names = sorted(os.listdir(self.SYS_NET))
        except OSError:
            return out
        addrs = {}
        try:
            r = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5)
            for line in (r.stdout or "").splitlines():
                m = re.match(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line)
                if m:
                    addrs.setdefault(m.group(1).split("@")[0], []).append(m.group(2))
        except (OSError, subprocess.SubprocessError):
            pass
        for n in names:
            if n.startswith(self.SKIP):
                continue
            base = "%s/%s" % (self.SYS_NET, n)
            try:
                if (read(base + "/type", "") or "").strip() != "1" or os.path.isdir(base + "/wireless") or os.path.isdir(base + "/phy80211") \
                        or os.path.isdir(base + "/bridge") or not os.path.exists(base + "/device"):
                    continue
                mac = (read(base + "/address", "") or "").strip().lower()
            except OSError:
                continue
            if self.MAC_RE.match(mac):
                out.append({"name": n, "mac": mac, "addrs": addrs.get(n, [])})
        return out

    def installed(self):
        return os.path.exists(self.UNIT)

    def _conf(self):
        try:
            d = json.loads(read(self.CONF, "") or "{}")
            if isinstance(d, dict) and self.MAC_RE.match(str(d.get("mac"))) and isinstance(d.get("addr"), str) and isinstance(d.get("prefix"), int):
                return d
        except ValueError:
            pass
        return None

    def status(self):
        if self.demo:
            f = self.fake
            return {"installed": True, "enabled": f["enabled"], "mac": f["mac"], "addr": f["addr"], "prefix": f["prefix"], "present": f["enabled"], "error": "",
                    "cards": [{"name": "eth0", "mac": "aa:bb:cc:00:11:22", "addrs": ["192.168.1.20/24"]}, {"name": "eth1", "mac": "aa:bb:cc:00:11:33", "addrs": ["192.168.50.20/24"]}]}
        c = self._conf()
        cards = self.cards()
        present = bool(c) and any(x["mac"] == c["mac"] and "%s/%d" % (c["addr"], c["prefix"]) in x["addrs"] for x in cards)
        return {"installed": self.installed(), "enabled": bool(c), "mac": (c or {}).get("mac", ""), "addr": (c or {}).get("addr", ""), "prefix": (c or {}).get("prefix", 24),
                "present": present, "error": "", "cards": cards}

    def set(self, enable, mac=None, addr=None, prefix=None, ack=False):
        if not isinstance(enable, bool):
            raise ValueError("Ungültige Anfrage")
        if enable and ack is not True:
            raise ValueError("Bitte zuerst bestätigen, dass die Änderung auf eigene Verantwortung erfolgt.")
        if enable:
            addr = self.check(mac, addr, prefix)
        if self.demo:
            self.fake.update({"enabled": enable, "mac": mac or self.fake["mac"], "addr": addr or "", "prefix": prefix or 24})
            return self.status()
        if not self.installed():
            raise ValueError("Kommt mit dem nächsten Software-Update.")
        t0 = int(self.clock())
        req = {"enable": True, "mac": mac, "addr": addr, "prefix": prefix} if enable else {"enable": False}
        tmp = self.req + ".tmp"
        with open(tmp, "w") as f:
            json.dump(req, f)
        os.replace(tmp, self.req)
        end = self.clock() + self.WAIT
        while self.clock() < end:
            try:
                st = json.loads(read(self.STATUS, "") or "{}")
            except ValueError:
                st = {}
            if isinstance(st, dict) and isinstance(st.get("time"), int) and st["time"] >= t0:
                if not st.get("ok"):
                    raise ValueError(str(st.get("error") or "Fehler")[:200])
                return self.status()
            self.sleep(0.4)
        raise ValueError("Keine Antwort vom Helfer.")


class UiAccess:
    TTL = 5.0

    def __init__(self, path, demo=False, lister=None):
        self.path, self.demo, self.lister = path, demo, lister or client_wifi_list
        self.lock = threading.Lock()
        self.block = False
        self._cache = (-1e9, [])
        try:
            with open(path) as f:
                self.block = json.load(f).get("block_client_wifi") is True
        except (OSError, ValueError, AttributeError):
            pass

    def clients(self):
        if self.demo:
            return [{"iface": "wlan0", "ip": "10.1.1.20"}]
        with self.lock:
            now = time.monotonic()
            if now - self._cache[0] > self.TTL:
                try:
                    self._cache = (now, self.lister())
                except Exception:
                    self._cache = (now, [])
            return list(self._cache[1])

    def refuses(self, local_ip):
        return bool(self.block and local_ip and any(c["ip"] == local_ip for c in self.clients()))

    def status(self, local_ip=None):
        cl = self.clients()
        return {"block_client_wifi": self.block, "client_wifi": cl, "on_client": bool(local_ip and any(c["ip"] == local_ip for c in cl))}

    def set(self, block, local_ip=None):
        if not isinstance(block, bool):
            raise ValueError("ja oder nein")
        if block and local_ip and any(c["ip"] == local_ip for c in self.clients()):
            raise ValueError("Du bist gerade über ein Gast-WLAN verbunden. Sonst sperrst du dich aus: Verbinde dich zuerst über Ethernet, den Hotspot der Box oder Tailscale.")
        self.block = block
        if not self.demo:
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"block_client_wifi": block}, f)
            os.replace(tmp, self.path)
        return self.status(local_ip)


TAILNET = ipaddress.ip_network("100.64.0.0/10")


def client_origin(peer, forwarded=False):
    try:
        a = ipaddress.ip_address(str(peer).split("%")[0])
    except ValueError:
        return "extern"
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    if a.is_loopback:
        return "extern" if forwarded else "lokal"
    if a.version == 4 and a in TAILNET:
        return "extern"
    return "lokal" if (a.is_private or a.is_link_local) else "extern"


class PreviewAccess:
    EXT_WIDTH = 320
    EXT_FPS = 10
    EXT_MAX = 1

    def __init__(self, path, demo=False):
        self.path, self.demo = path, demo
        self.lock = threading.Lock()
        self.external = False
        self.ext_viewers = 0
        try:
            with open(path) as f:
                self.external = json.load(f).get("external") is True
        except (OSError, ValueError, AttributeError):
            pass

    def set(self, external):
        if not isinstance(external, bool):
            raise ValueError("ja oder nein")
        with self.lock:
            self.external = external
            if not self.demo:
                tmp = self.path + ".tmp"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump({"external": external}, f)
                os.replace(tmp, self.path)
        return self.status()

    def status(self):
        with self.lock:
            return {"external": self.external, "ext_viewers": self.ext_viewers, "ext_width": self.EXT_WIDTH, "ext_fps": self.EXT_FPS, "ext_max": self.EXT_MAX}

    def allowed(self, origin):
        return origin != "extern" or self.external

    def acquire(self, origin):
        if origin != "extern":
            return True
        with self.lock:
            if not self.external or self.ext_viewers >= self.EXT_MAX:
                return False
            self.ext_viewers += 1
            return True

    def release(self, origin):
        if origin == "extern":
            with self.lock:
                self.ext_viewers = max(0, self.ext_viewers - 1)


def source_allowed(ip):
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return bool(a.is_loopback or a.is_private or a.is_link_local or (a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10")))
