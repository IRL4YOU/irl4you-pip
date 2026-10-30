
import json
import os
import re
import socket
import subprocess
import time

from srv.util import ttl_cached


NET_PREFIXES = ("eth", "en", "wlan", "wl", "usb", "wwan", "ppp", "rndis")


NET_SKIP = ("docker", "veth", "br-", "virbr", "tailscale", "tun", "tap", "lo")


ARP_STATUS = "/run/pipbox-arp/status.json"


ARP_STATUS_MAX_AGE = 60


NET_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")


def net_conflict(path=None):
    try:
        with open(path or ARP_STATUS, "rb") as f:
            d = json.loads(f.read(4096).decode("utf-8", "replace"))
        if not isinstance(d, dict) or not isinstance(d.get("time"), int) or abs(time.time() - d["time"]) > ARP_STATUS_MAX_AGE:
            return None
        names, nets = d.get("conflict"), d.get("nets")
        if not isinstance(names, list):
            return None
        cards = [n for n in names if isinstance(n, str) and IFACE_NAME_RE.match(n)][:8]
        if len(cards) < 2:
            return None
        nets = [n for n in nets if isinstance(n, str) and NET_RE.match(n)][:4] if isinstance(nets, list) else []
        return {"cards": cards, "nets": nets}
    except (OSError, ValueError):
        return None


def lan_ip():
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("203.0.113.1", 9))
        ip = sk.getsockname()[0]
        sk.close()
        return ip
    except OSError:
        return "127.0.0.1"


NET_LABELS = {}


def net_label(name):
    if name.startswith(("wl",)):
        return f"WLAN ({name})"
    try:
        if "/usb" in os.path.realpath(f"/sys/class/net/{name}/device"):
            return f"USB-Router ({name})"
    except OSError:
        pass
    return f"Ethernet ({name})"


FIXED_ALIAS = {"eth1": "192.168.80.50"}


def fixed_ip(name):
    want = FIXED_ALIAS.get(name)
    if not want:
        return None
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", name],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return want if f" {want}/" in out else None


def mac_suffix(iface):
    try:
        with open(f"/sys/class/net/{iface}/address") as f:
            parts = f.read().strip().upper().split(":")
    except (OSError, ValueError):
        return ""
    if len(parts) != 6 or not all(re.fullmatch(r"[0-9A-F]{2}", x) for x in parts):
        return ""
    return parts[4] + parts[5]


def iface_ips():
    return [dict(x) for x in ttl_cached("iface_ips", 3.0, _iface_ips_raw)]


def _iface_ips_raw():
    try:
        import fcntl
        import struct
        names = sorted(os.listdir("/sys/class/net"))
    except (ImportError, OSError):
        return []
    out = []
    for name in names:
        if name == "lo" or name.startswith(("docker", "veth", "br-", "virbr", "p2p", "tailscale", "tun", "tap", "wg", "zt")):
            continue
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ip = socket.inet_ntoa(fcntl.ioctl(sk.fileno(), 0x8915,
                                              struct.pack("256s", name[:15].encode()))[20:24])
            sk.close()
        except OSError:
            continue
        out.append({"iface": name, "ip": ip, "cam_ip": fixed_ip(name) or ip, "label": NET_LABELS.get(name) or net_label(name),
                    "wifi": os.path.isdir(f"/sys/class/net/{name}/wireless")})
    return out


LINKS_FILE = "/run/pipbox-send/srtla-links.txt"


def uplink_states(selected):
    try:
        if time.time() - os.stat(LINKS_FILE).st_mtime > 20:
            return {}
        with open(LINKS_FILE) as f:
            lines = f.readlines()[-60:]
    except OSError:
        return {}
    last = {}
    for ln in lines:
        m = re.match(r"\S+ links: (\S+) (?:stall=\d+ )?srtt=(-?\d+)ms .*? (genutzt|reserve) ", ln)
        if m:
            last[m.group(1)] = (int(m.group(2)), m.group(3))
    by_ip = {o["ip"]: o["iface"] for o in iface_ips()}
    seen = {by_ip[ip]: v for ip, v in last.items() if ip in by_ip}
    out = {}
    for n in selected:
        v = seen.get(n)
        if v is None:
            out[n] = {"state": "aus", "srtt": None}
        else:
            out[n] = {"state": "an" if v[1] == "genutzt" else "reserve", "srtt": v[0] if v[0] >= 0 else None}
    return out


def _alert_text(a):
    if a.get("text"):
        return str(a["text"])[:120]
    kind = a.get("kind")
    if kind == "usb":
        return "USB-Gerät getrennt"
    if kind == "cam":
        return "Kamera %s ohne Signal" % (a.get("name") or "")
    if kind == "hdmi":
        return "HDMI-Kamera ohne Signal"
    return "Meldung"


IFACE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")
