#!/usr/bin/env python3
"""Root-Helfer: feste Zusatzadresse für ein LAN-Kabel (läuft über pipbox-netaddr.path oder von install.sh mit --remove).

Wunsch aus Issue #65: Die Box bekommt ihre Adresse weiter per DHCP vom Router; zusätzlich kann sie eine **feste** Adresse bekommen (zum Beispiel, wenn sie am selben
Router hängt wie andere Geräte und unter einer bekannten Adresse erreichbar sein soll). Die feste Adresse gehört zur **MAC-Adresse** der Netzkarte, nicht zum Namen
(Namen wie eth0 und eth1 wechseln bei USB-Adaptern). Die DHCP-Adresse, die Standardroute und der Sendeweg bleiben unberührt: Es wird nur eine Adresse hinzugefügt.

Auslösedatei <STATE>/netaddr-request (JSON, höchstens 512 Byte, ohne Verweisen zu folgen, wird gelöscht):
  {"enable": true, "mac": "aa:bb:cc:dd:ee:ff", "addr": "192.168.1.50", "prefix": 24}   oder   {"enable": false}
Streng geprüft: nur IPv4 aus den privaten Bereichen (10/8, 172.16/12, 192.168/16), Länge 8 bis 30, nicht die Netz- oder Rundrufadresse, die MAC gehört zu einer
verkabelten Netzkarte (kein WLAN, kein Tailscale, kein Container), die Adresse ist noch nirgends auf der Box vergeben und das Netz überschneidet sich **nicht** mit dem
Netz einer anderen Netzkarte (getrennter Adressraum; sonst gerieten Wege durcheinander). Es gibt kein Gateway und keine Route.

Dauerhaft: Das Skript /etc/network/if-up.d/pipbox-extra-ip (geschrieben von diesem Helfer, mit fest eingesetzten, geprüften Werten) setzt die Adresse bei jedem
Hochfahren der Netzkarte wieder (auch wenn ifplugd oder der DHCP-Dienst die Karte neu starten); es vergleicht die MAC der Karte. Zum Ausschalten werden Skript,
Einstellung und Adresse entfernt. Ergebnis für die Oberfläche: /run/pipbox-netaddr/status.json."""
import fcntl
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/netaddr-request"
RUN = "/run/pipbox-netaddr"
STATUS = f"{RUN}/status.json"
CONF_DIR = "/etc/pipbox"
CONF = f"{CONF_DIR}/extra-ip.json"
HOOK_DIR = "/etc/network/if-up.d"
HOOK = f"{HOOK_DIR}/pipbox-extra-ip"
LOCK = "/run/pipbox-netaddr.lock"
SYS_NET = "/sys/class/net"
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
PRIVATE = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"), ipaddress.ip_network("192.168.0.0/16"))
SKIP_PREFIX = ("lo", "docker", "veth", "br-", "virbr", "p2p", "tailscale", "tun", "tap", "wg", "zt", "wlan", "wl")
LABEL = "pb"


class Refuse(Exception):
    """Anfrage abgelehnt; der Text ist für die Oberfläche gedacht."""


def read_req(path, limit=512):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def write_file(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        os.unlink(tmp)
    except OSError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def ip(*args):
    return subprocess.run(["ip", *args], capture_output=True, text=True, timeout=15)


def wired():
    """{Name: MAC} der verkabelten, echten Netzkarten (kein WLAN, Tailscale, Container oder Brücke)."""
    out = {}
    try:
        names = sorted(os.listdir(SYS_NET))
    except OSError:
        return out
    for n in names:
        if n.startswith(SKIP_PREFIX):
            continue
        base = f"{SYS_NET}/{n}"
        try:
            if open(f"{base}/type").read().strip() != "1":
                continue
            if os.path.isdir(f"{base}/wireless") or os.path.isdir(f"{base}/phy80211") or os.path.isdir(f"{base}/bridge") or not os.path.exists(f"{base}/device"):
                continue
            mac = open(f"{base}/address").read().strip().lower()
        except OSError:
            continue
        if MAC_RE.match(mac):
            out[n] = mac
    return out


def addresses():
    """[(Name, Adresse, Netz)] aller IPv4-Adressen der Box (ohne lo)."""
    out = []
    r = ip("-4", "-o", "addr", "show")
    for line in (r.stdout or "").splitlines():
        m = re.match(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if m and m.group(1) != "lo":
            try:
                out.append((m.group(1).split("@")[0], ipaddress.ip_address(m.group(2)), ipaddress.ip_network("%s/%s" % (m.group(2), m.group(3)), strict=False)))
            except ValueError:
                pass
    return out


def validate(mac, addr, prefix):
    """Gibt (Netzkartenname, Adresse, Länge) zurück oder wirft Refuse."""
    if not isinstance(mac, str) or not MAC_RE.match(mac):
        raise Refuse("Ungültige MAC-Adresse")
    if not isinstance(addr, str) or isinstance(prefix, bool) or not isinstance(prefix, int):
        raise Refuse("Ungültige Anfrage")
    try:
        a = ipaddress.IPv4Address(addr)
    except ValueError:
        raise Refuse("Ungültige IP-Adresse")
    if not any(a in n for n in PRIVATE):
        raise Refuse("Nur Adressen aus den privaten Bereichen sind erlaubt (10.…, 172.16.… bis 172.31.…, 192.168.…).")
    if not 8 <= prefix <= 30:
        raise Refuse("Die Netzlänge muss zwischen 8 und 30 liegen (üblich: 24).")
    net = ipaddress.ip_network("%s/%d" % (a, prefix), strict=False)
    if a == net.network_address or a == net.broadcast_address:
        raise Refuse("Das ist die Netz- oder Rundrufadresse, nicht die einer Geräteadresse.")
    cards = wired()
    names = [n for n, m in cards.items() if m == mac]
    if not names:
        raise Refuse("Diese Netzkarte (MAC-Adresse) ist nicht eingesteckt oder kein LAN-Kabel.")
    name = names[0]
    cur = current() or {}
    mine = (cur.get("mac"), cur.get("addr"), cur.get("prefix")) == (mac, str(a), prefix)                 # die eigene, schon gesetzte Adresse erneut übernehmen ist in Ordnung
    for n, other, onet in addresses():
        if other == a and not (mine and n == name):
            raise Refuse("Die Adresse ist auf dieser Box schon vergeben (%s)." % n)
        if n != name and onet.overlaps(net):
            raise Refuse("Das Netz überschneidet sich mit dem Netz von %s. Die feste Adresse muss in einem getrennten Adressraum liegen." % n)
    return name, str(a), prefix


def hook_text(mac, addr, prefix):
    return (b"#!/bin/sh\n# IRL4YOU BOX: feste Zusatzadresse (von pipbox-netaddr.py geschrieben; zum Entfernen in der Oberflaeche ausschalten)\n".decode("ascii") +
            '[ "$ADDRFAM" = "inet" ] || exit 0\n'
            '[ -r "/sys/class/net/$IFACE/address" ] || exit 0\n'
            '[ "$(cat "/sys/class/net/$IFACE/address")" = "%s" ] || exit 0\n'
            'LB=""\n[ ${#IFACE} -le 12 ] && LB="label $IFACE:pb"\n'
            'if ip addr replace %s/%d dev "$IFACE" $LB 2>/dev/null; then\n'
            '  logger -t pipbox-extra-ip "gesetzt auf $IFACE (${MODE:-?} ${PHASE:-?} ${METHOD:-?})" 2>/dev/null || true\n'
            'else\n'
            '  logger -t pipbox-extra-ip "FEHLER beim Setzen auf $IFACE (${MODE:-?} ${PHASE:-?} ${METHOD:-?})" 2>/dev/null || true\n'
            'fi\nexit 0\n' % (mac, addr, prefix))


def status(**kw):
    os.makedirs(RUN, exist_ok=True)
    kw["time"] = int(time.time())
    write_file(STATUS, json.dumps(kw))


def current():
    try:
        d = json.loads(open(CONF).read())
        if isinstance(d, dict) and MAC_RE.match(str(d.get("mac"))):
            return d
    except (OSError, ValueError):
        pass
    return None


def remove_address():
    d = current()
    if not d:
        return
    names = [n for n, m in wired().items() if m == d["mac"]]
    for n in names:
        ip("addr", "del", "%s/%d" % (d["addr"], int(d["prefix"])), "dev", n)


def disable():
    remove_address()
    for p in (HOOK, CONF):
        try:
            os.unlink(p)
        except OSError:
            pass
    status(ok=True, enabled=False, error="")


def enable(mac, addr, prefix):
    name, addr, prefix = validate(mac, addr, prefix)
    if current() and (current().get("mac"), current().get("addr"), int(current().get("prefix", 0))) != (mac, addr, prefix):
        remove_address()                                                         # alte Adresse weg, bevor die neue kommt
    os.makedirs(CONF_DIR, exist_ok=True)
    write_file(CONF, json.dumps({"mac": mac, "addr": addr, "prefix": prefix}) + "\n")
    os.makedirs(HOOK_DIR, exist_ok=True)
    write_file(HOOK, hook_text(mac, addr, prefix), 0o755)
    args = ["addr", "replace", "%s/%d" % (addr, prefix), "dev", name]
    if len(name) + len(LABEL) + 1 <= 15:
        args += ["label", "%s:%s" % (name, LABEL)]
    r = ip(*args)
    if r.returncode != 0:
        raise Refuse("Die Adresse konnte nicht gesetzt werden: %s" % (r.stderr or "").strip()[:120])
    status(ok=True, enabled=True, mac=mac, addr=addr, prefix=prefix, iface=name, error="")


def handle(req):
    if not isinstance(req, dict) or not isinstance(req.get("enable"), bool):
        raise Refuse("Ungültige Anfrage")
    if req["enable"]:
        enable(req.get("mac"), req.get("addr"), req.get("prefix"))
    else:
        disable()


def main(argv):
    if len(argv) == 2 and argv[1] == "--remove":                              # von install.sh beim Entfernen
        disable()
        return 0
    try:
        raw = read_req(REQ)
    except OSError:
        return 0
    try:
        os.remove(REQ)
    except OSError:
        pass
    lock = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    try:
        handle(json.loads(raw))
    except Refuse as e:
        status(ok=False, error=str(e), enabled=bool(current()))
    except (ValueError, subprocess.SubprocessError, OSError) as e:
        status(ok=False, error="Fehler: %s" % str(e)[:100], enabled=bool(current()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
