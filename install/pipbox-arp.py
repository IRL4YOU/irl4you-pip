#!/usr/bin/env python3
"""pipbox-arp: eindeutige ARP-Antworten, wenn zwei Netzkarten im selben Netz hängen.

Aufruf:
  watch      Dauerlauf
  once       einmal prüfen und anwenden
  uninstall  gesetzte Werte zurücksetzen
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
STATE = f"{RUN}/state.json"
STATUS = f"{RUN}/status.json"
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
    try:
        r = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        p = line.split()
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
    entry = state.pop(iface, None)
    if not entry:
        return False
    for key in WANT:
        mine, old = entry.get("set", {}).get(key), entry.get("old", {}).get(key)
        if isinstance(mine, int) and isinstance(old, int) and read_int(conf_path(iface, key)) == mine:
            write_int(conf_path(iface, key), old)
    return True


def step(state=None):
    state = load_state() if state is None else state
    addrs = addresses()
    if addrs is None:
        return state
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
        except Exception as e:
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
