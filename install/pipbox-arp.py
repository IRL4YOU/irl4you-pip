#!/usr/bin/env python3
"""Root-Helfer: eindeutige Antworten auf Adressanfragen (ARP), wenn zwei Netzkarten im selben Netz hängen (11. Oktober 2026).

Hängt die Box mit zwei Karten im selben IPv4-Netz (zum Beispiel WLAN und ein USB-Tether am selben Router), beantwortet Linux die Frage "Wer hat 192.168.80.135?" standardmäßig
auf jeder dieser Karten. Kameras und Router merken sich dann zeitweise die falsche Hardware-Adresse; das Bild läuft über die falsche Karte oder ins Leere, die Kamera
wird von nginx wegen "drop idle publisher" entfernt, und die Bluetooth-Verbindung beginnt von vorn (ROCK 5B Plus, DJI Action 4 und Pocket 3).

Dieser Dienst prüft alle paar Sekunden, ob sich zwei Karten im selben Netz überschneiden, und stellt **nur für diese Karten** arp_ignore=1 und arp_announce=2 ein
(jede Karte antwortet nur für ihre eigene Adresse und meldet sich mit der passenden Absenderadresse). Überschneiden sie sich nicht mehr (oder verschwindet eine Karte), werden
die alten Werte wieder hergestellt, aber nur, wenn **wir** sie gesetzt haben und sie noch so stehen, wie wir sie hinterlassen haben. Eine Einstellung des Nutzers, zum Beispiel eine Datei in /etc/sysctl.d,
bleibt unangetastet. Es gibt keine Datei in /etc/sysctl.d und nichts Dauerhaftes; der Merkzettel liegt in /run (wie die Werte selbst nach jedem Neustart weg).

Auf einer Box ohne Überschneidung tut der Dienst nichts außer lesen.

Zusätzlich meldet er in status.json einen **Konflikt**, aber nur dort, wo derselbe Adressbereich bei **verschiedenen Geräten** benutzt wird (zum Beispiel zwei Modems oder Handy-Hotspots, die beide 192.168.8.x vergeben): Die Hardware-Adresse des Routers (Standardgateway) ist an den beiden Karten verschieden. Hängt die Box über LAN und WLAN am selben Router (gleiche Hardware-Adresse), ist das kein Konflikt: Die ARP-Einstellung genügt, und die Oberfläche warnt nicht. Lässt sich eine der beiden Adressen nicht ermitteln, bleibt es still.

Aufruf (nur durch pipbox-arp.service, keine Eingaben von außen):
  watch      Dauerlauf, prüft alle INTERVAL Sekunden (beim Beenden bleibt der Zustand bestehen, damit ein Neustart des Dienstes beim Update keine Lücke reißt)
  once       einmal prüfen und anwenden (für Tests und von Hand)
  uninstall  alle von uns gesetzten Werte zurücksetzen (beim Entfernen der Software)
"""
import fcntl
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import time

CONF = "/proc/sys/net/ipv4/conf"
RUN = "/run/pipbox-arp"
STATE = f"{RUN}/state.json"             # {Karte: {"ignore": alt, "announce": alt}}: was wir geändert haben und womit es vorher belegt war
STATUS = f"{RUN}/status.json"           # für die Oberfläche: welche Karten sich überschneiden
LOCK = "/run/pipbox-arp.lock"
INTERVAL = 10
SKIP_PREFIX = ("lo", "docker", "veth", "br-", "virbr", "p2p", "tailscale", "tun", "tap", "wg", "zt")
IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
WANT = {"arp_ignore": 1, "arp_announce": 2}


def log(msg):
    print(f"pipbox-arp: {msg}", flush=True)


def read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def write_int(path, value):
    try:
        with open(path, "w") as f:
            f.write(f"{value}\n")
        return True
    except OSError as e:
        log(f"{path}: {e}")
        return False


def addresses():
    """[(Karte, Netz)] aller echten IPv4-Netze der Karten (ohne lo, Tailscale, Container, VPN, Verbindungslokales und Punkt-zu-Punkt-Adressen)."""
    try:
        r = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        p = line.split()
        # "3: wlan0    inet 192.168.80.135/24 brd 192.168.80.255 scope global dynamic wlan0 ..."
        if len(p) < 4 or p[2] != "inet":
            continue
        name = p[1].rstrip(":")
        if not IFACE_RE.match(name) or name.startswith(SKIP_PREFIX) or "peer" in p:
            continue
        try:
            net = ipaddress.ip_interface(p[3]).network
        except ValueError:
            continue
        if net.prefixlen >= 31 or net.is_link_local:
            continue
        out.append((name, net))
    return out


MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def ip_lines(*args):
    try:
        r = subprocess.run(["ip", "-4", "-o", *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    return r.stdout.splitlines() if r.returncode == 0 else []


def gateway_mac(iface):
    """Hardware-Adresse des Standardrouters an dieser Karte (klein geschrieben) oder None, wenn es keinen gibt oder sie nicht bekannt ist."""
    gw = None
    for ln in ip_lines("route", "show", "default", "dev", iface):
        p = ln.split()
        if "via" in p and p.index("via") + 1 < len(p):
            gw = p[p.index("via") + 1]
            break
    if not gw:
        return None
    for ln in ip_lines("neigh", "show", "dev", iface):
        p = ln.split()
        if p and p[0] == gw and "lladdr" in p and p.index("lladdr") + 1 < len(p):
            mac = p[p.index("lladdr") + 1].lower()
            return mac if MAC_RE.match(mac) else None
    return None


def conflicts(addrs):
    """(Karten, Netze) bei denen sich die Adressbereiche zweier Karten überschneiden und ihre Router verschiedene Geräte sind (verschiedene Hardware-Adresse)."""
    names, nets, cache = set(), set(), {}

    def mac(n):
        if n not in cache:
            cache[n] = gateway_mac(n)
        return cache[n]
    for i, (a, na) in enumerate(addrs):
        for b, nb in addrs[i + 1:]:
            if a != b and na.overlaps(nb):
                ma, mb = mac(a), mac(b)
                if ma and mb and ma != mb:
                    names.update((a, b))
                    nets.update((str(na), str(nb)))
    return sorted(names), sorted(nets)


def overlapping(addrs):
    """Karten, deren Netz sich mit dem einer anderen Karte überschneidet."""
    names = set()
    for i, (a, na) in enumerate(addrs):
        for b, nb in addrs[i + 1:]:
            if a != b and na.overlaps(nb):
                names.update((a, b))
    return sorted(names)


def load_state():
    try:
        with open(STATE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state, overlap, conflict=(), nets=()):
    os.makedirs(RUN, exist_ok=True)
    status = {"overlap": overlap, "conflict": list(conflict), "nets": list(nets), "fixed": sorted(state), "time": int(time.time())}
    for path, data in ((STATE, state), (STATUS, status)):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)


def conf_path(iface, key):
    return f"{CONF}/{iface}/{key}"


def apply_card(state, iface):
    """arp_ignore und arp_announce dieser Karte hochsetzen (nie herabsetzen) und die alten Werte merken."""
    old = {}
    for key, want in WANT.items():
        cur = read_int(conf_path(iface, key))
        if cur is None:
            return False
        old[key] = cur
    changed = False
    for key, want in WANT.items():
        if old[key] < want:
            if not write_int(conf_path(iface, key), want):
                return False
            changed = True
    if changed:
        state[iface] = {"old": old, "set": {k: max(old[k], w) for k, w in WANT.items()}}
    return changed


def release_card(state, iface):
    """Die alten Werte zurückschreiben, aber nur dort, wo noch genau unser Wert steht."""
    entry = state.pop(iface, None)
    if not entry:
        return False
    for key in WANT:
        mine, old = entry.get("set", {}).get(key), entry.get("old", {}).get(key)
        if isinstance(mine, int) and isinstance(old, int) and read_int(conf_path(iface, key)) == mine:
            write_int(conf_path(iface, key), old)
    return True


def step(state=None):
    """Einmal prüfen und anwenden. Gibt den Zustand zurück."""
    state = load_state() if state is None else state
    addrs = addresses()
    if addrs is None:
        return state                                   # `ip` nicht lesbar: nichts ändern
    overlap = overlapping(addrs)
    for iface in overlap:
        if iface not in state and apply_card(state, iface):
            log(f"{iface} liegt mit einer anderen Karte im selben Netz: arp_ignore=1, arp_announce=2")
    for iface in sorted(set(state) - set(overlap)):
        if release_card(state, iface):
            log(f"{iface}: keine Überschneidung mehr, alte Werte zurück")
    conflict, nets = conflicts(addrs) if overlap else ([], [])
    save_state(state, overlap, conflict, nets)
    return state


def lock():
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("pipbox-arp: läuft schon")
    return fd


def watch():
    _fd = lock()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while True:
        try:
            step()
        except Exception as e:                         # der Dienst darf nie an einem Einzelfall sterben
            log(f"Fehler: {e}")
        time.sleep(INTERVAL)


def uninstall():
    state = load_state()
    for iface in list(state):
        release_card(state, iface)
    for path in (STATE, STATUS):
        try:
            os.unlink(path)
        except OSError:
            pass
    log("zurückgesetzt")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "watch"
    if cmd == "watch":
        watch()
    elif cmd == "once":
        _fd = lock()
        step()
    elif cmd == "uninstall":
        uninstall()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
