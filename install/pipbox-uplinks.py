#!/usr/bin/env python3
"""pipbox-uplinks: prüft jede Internetleitung der Box und lässt die Box selbst (Tailscale, Updates, Zeit) die nächste nehmen, wenn eine ausfällt.

Aufruf:
  watch      Dauerlauf (alle 10 Sekunden)
  once       einmal prüfen und anwenden
  dry        einmal prüfen, nichts ändern, nur zeigen, was geändert würde
  uninstall  unsere Routen entfernen (Metrik 100 + Karte und 900 + Karte)

Was es tut:
  1. Je Netzkarte mit Adresse und Router wird mit drei kurzen Verbindungen (TCP) zu bekannten Adressen geprüft, ob über sie Internet ankommt. Die
     Verbindung wird an die Adresse der Karte gebunden, geht also genau den Weg, den auch die Sendewege (srtla) nehmen. Eine Antwort, auch eine
     Ablehnung (RST), zeigt, dass der Weg geht. Erst nach zwei Fehlschlägen in Folge gilt eine Leitung als ausgefallen, eine Antwort genügt, damit sie
     wieder gilt.
  2. Für die Box selbst steht je Karte eine Standardroute mit Metrik 100 + Nummer der Karte. Ist eine Leitung ausgefallen, bekommt sie Metrik 900 + Nummer:
     Die Box nimmt dann die nächste Leitung. Die Standardroute ohne Metrik, die der DHCP-Dienst der ersten Karte setzt, wird dafür durch die mit Metrik ersetzt.
     Die Sendewege (srtla) ändert das nicht: Ihre Quelladress-Regeln stehen vor der allgemeinen Tabelle.
  3. Stimmt die Tabelle einer Karte für die Sendewege nicht (kein Router darin, falsche Karte, fehlende Regel), wird sie aus dem Router der Karte neu gebaut.
  4. Verbindet sich Tailscale länger als 45 Sekunden nicht, obwohl mehrere Leitungen Internet haben, wird nacheinander jede Leitung zur bevorzugten (Metrik 20 + Karte)
     und ausprobiert; die, mit der Tailscale verbindet, bleibt bevorzugt, bis sie ausfällt.
  5. Das Ergebnis steht in /run/pipbox-uplinks/status.json (die Oberfläche zeigt daraus Meldungen je Leitung).
WLAN, Mobilfunk und PPP-Verbindungen werden geprüft und angezeigt, ihre Routen aber nicht angefasst.
"""
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time

RUN = "/run/pipbox-uplinks"
STATUS = f"{RUN}/status.json"
INTERVAL = 10
FAILS_NEEDED = 2
PROBE_TIMEOUT = 1.5
TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 443), ("9.9.9.9", 443), ("1.1.1.1", 53))
ROUTE_TEST = "1.1.1.1"
SKIP_PREFIX = ("lo", "docker", "veth", "br-", "virbr", "p2p", "tailscale", "tun", "tap", "wg", "zt")
IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
MANAGED_RE = re.compile(r"^(eth|en|usb|rndis)[A-Za-z0-9_.-]*$")
GOOD_BASE, BAD_BASE, PREF_BASE = 100, 900, 20
LINKS_FILE = "/run/pipbox-send/srtla-links.txt"
SRT_MUTE_AFTER = 45             # Sekunden, die der SRT-Server auf einer Leitung schweigen muss, bevor sie für die Box selbst nachrangig wird
TS_WAIT = 45                    # Sekunden ohne Tailscale, bevor die nächste Leitung drankommt
RT_TABLES = ("/etc/iproute2/rt_tables", "/etc/iproute2/rt_tables.d")


def log(msg):
    print(f"pipbox-uplinks: {msg}", flush=True)


def run(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ------------------------------------------------------------------ Lesen (reine Funktionen, gut zu prüfen)

def parse_addrs(text):
    """ip -4 -o addr show scope global -> {Karte: [{ip, plen, dynamic, extra}]}; "extra" = zweite Adresse oder Zusatzname (eth1:pb, eth0:1)."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^\d+:\s+(\S+)\s+inet (\d+\.\d+\.\d+\.\d+)/(\d+)\s(.*)$", line)
        if not m:
            continue
        name, ip, plen, rest = m.group(1), m.group(2), int(m.group(3)), m.group(4)
        if not IFACE_RE.match(name) or name.startswith(SKIP_PREFIX):
            continue
        head = rest.split("\\")[0]
        label = (head.split()[-1:] or [name])[0]
        out.setdefault(name, []).append({"ip": ip, "plen": plen, "dynamic": " dynamic" in " " + head,
                                         "extra": "secondary" in head.split() or ":" in label})
    return out


def uplink_addrs(addrs, gw):
    """Adressen der Karte, die im Netz ihres Routers liegen (nur über diese gehen Pakete zum Router); die zuerst, die per DHCP kam."""
    if not gw:
        return []
    out = []
    for a in addrs:
        try:
            if ipaddress.ip_address(gw) in ipaddress.ip_network(f"{a['ip']}/{a['plen']}", strict=False) and gw != a["ip"]:
                out.append(a)
        except ValueError:
            continue
    out.sort(key=lambda a: (not a["dynamic"], a["extra"]))
    return out


def parse_default_routes(text):
    """ip -4 route show default -> [{dev, via, metric}]"""
    out = []
    for line in text.splitlines():
        if not line.startswith("default"):
            continue
        dev = re.search(r"\bdev (\S+)", line)
        via = re.search(r"\bvia (\d+\.\d+\.\d+\.\d+)", line)
        met = re.search(r"\bmetric (\d+)", line)
        if dev:
            out.append({"dev": dev.group(1), "via": via.group(1) if via else None, "metric": int(met.group(1)) if met else 0})
    return out


def gateway_for(iface, addrs, defaults, table_defaults):
    """Router der Karte: aus den Standardrouten der Karte (niedrigste Metrik), sonst aus ihrer Tabelle; er muss im Netz einer ihrer Adressen liegen."""
    cands = sorted((r for r in defaults if r["dev"] == iface and r["via"]), key=lambda r: r["metric"]) + [r for r in table_defaults if r["dev"] == iface and r["via"]]
    for r in cands:
        if uplink_addrs(addrs, r["via"]):
            return r["via"]
    return None


def route_dev(text):
    m = re.search(r"\bdev (\S+)", text)
    return m.group(1) if m else None


def ifindex(iface):
    try:
        with open(f"/sys/class/net/{iface}/ifindex") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def table_names():
    names = set()
    paths = [RT_TABLES[0]]
    try:
        paths += [os.path.join(RT_TABLES[1], x) for x in sorted(os.listdir(RT_TABLES[1]))]
    except OSError:
        pass
    for p in paths:
        try:
            with open(p) as f:
                for line in f:
                    line = line.split("#")[0].split()
                    if len(line) >= 2:
                        names.add(line[1])
        except OSError:
            continue
    return names


# ------------------------------------------------------------------ Prüfen

ERR_TEXT = {
    "keine_adresse": "Keine Adresse (DHCP hat keine vergeben)",
    "kein_router": "Kein Router gefunden (keine Standardroute auf dieser Karte)",
    "keine_route": "Keine Route ins Internet über diese Karte",
    "zeitueberschreitung": "Keine Antwort aus dem Internet (Router oder Leitung ohne Internet)",
    "falsche_karte": "Pakete dieser Karte verlassen die Box über eine andere Karte (Quelladress-Routing falsch)",
}


def probe(ip, timeout=PROBE_TIMEOUT):
    """Eine Antwort (auch RST) von einem der Ziele = Weg ok. -> {ok, rtt_ms, err}"""
    errs = []
    for host, port in TARGETS:
        t0 = time.monotonic()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.settimeout(timeout)
            s.bind((ip, 0))
            s.connect((host, port))
            return {"ok": True, "rtt_ms": round((time.monotonic() - t0) * 1000), "err": None}
        except ConnectionRefusedError:
            return {"ok": True, "rtt_ms": round((time.monotonic() - t0) * 1000), "err": None}
        except ConnectionResetError:
            return {"ok": True, "rtt_ms": round((time.monotonic() - t0) * 1000), "err": None}
        except socket.timeout:
            errs.append("zeitueberschreitung")
        except OSError as e:
            errs.append("keine_route" if e.errno in (101, 113, 99) else "zeitueberschreitung")      # ENETUNREACH, EHOSTUNREACH, EADDRNOTAVAIL
        finally:
            s.close()
    err = "keine_route" if errs and all(e == "keine_route" for e in errs) else "zeitueberschreitung"
    return {"ok": False, "rtt_ms": None, "err": err}


def probe_all(addrs, timeout=PROBE_TIMEOUT):
    """Alle Karten gleichzeitig prüfen (je Karte ein Faden)."""
    res = {}

    def work(name, ip):
        res[name] = probe(ip, timeout)
    threads = [threading.Thread(target=work, args=(n, a[0]), daemon=True) for n, a in addrs.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout * len(TARGETS) + 2)
    for n in addrs:
        res.setdefault(n, {"ok": False, "rtt_ms": None, "err": "zeitueberschreitung"})
    return res


# ------------------------------------------------------------------ Entscheiden (reine Funktionen)

def tailscale_state(text):
    """Ausgabe von "tailscale status --json" -> True (verbunden), False (läuft, aber ohne Verbindung zur Zentrale) oder None (nicht eingerichtet/gestoppt)."""
    try:
        d = json.loads(text)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    if d.get("BackendState") != "Running":
        return None if d.get("BackendState") in ("Stopped", "NeedsLogin", "NoState", "NeedsMachineAuth") else False
    me = d.get("Self") or {}
    return False if me.get("Online") is False else True


def srtla_evidence(text, by_ip):
    """Letzte Zeile je Sendeweg aus srtla-links.txt -> {Karte: True (Server antwortet) / False (keine Antwort, srtt=-1)}."""
    last = {}
    for ln in text.splitlines()[-60:]:
        m = re.match(r"\S+ links: (\S+) (?:stall=\d+ )?srtt=(-?\d+)ms ", ln)
        if m and m.group(1) in by_ip:
            last[by_ip[m.group(1)]] = int(m.group(2)) >= 0
    return last


class Mute:
    """Merkt sich Leitungen, auf denen Internet (TCP) geht, der SRT-Server (UDP) aber schweigt, während eine andere Leitung ihn erreicht.
    Das sind Leitungen, die für Tailscale (und die Freigabe von außen) nicht taugen. Bleibt bestehen, bis der Server dort antwortet oder die Leitung ausfällt."""

    def __init__(self, after=SRT_MUTE_AFTER):
        self.after = after
        self.since = {}
        self.muted = set()

    def update(self, now, evidence, alive):
        """evidence: srtla_evidence oder {} (kein Senden); alive: Karten mit Internet."""
        if evidence and any(evidence.values()):
            for n, ok in evidence.items():
                if ok:
                    self.since.pop(n, None)
                    self.muted.discard(n)
                elif n in alive:
                    t = self.since.setdefault(n, now)
                    if now - t >= self.after:
                        self.muted.add(n)
        else:
            self.since.clear()                  # kein Sendeweg antwortet: nichts daraus schließen
        self.muted &= set(alive)
        for n in [n for n in self.since if n not in alive]:
            del self.since[n]
        return set(self.muted)


class Rotor:
    """Hilft Tailscale zur nächsten Leitung: Ist Tailscale länger als TS_WAIT Sekunden ohne Verbindung, obwohl Leitungen Internet haben,
    wird die nächste Leitung zur bevorzugten (kleinste Metrik). Verbindet es, bleibt sie es, bis sie ausfällt."""

    def __init__(self, wait=TS_WAIT):
        self.wait = wait
        self.pref = None
        self.tried = []
        self.bad_since = None
        self.switched = 0

    def update(self, now, ts, good):
        """ts: True/False/None (tailscale_state); good: Karten mit Internet in fester Reihenfolge. -> bevorzugte Karte oder None"""
        if self.pref and self.pref not in good:
            self.pref, self.bad_since = None, None
        if ts is not False or len(good) < 2:
            self.bad_since = None
            if ts:
                self.tried = []
            return self.pref
        if self.bad_since is None:
            self.bad_since = now
            return self.pref
        if now - self.bad_since < self.wait:
            return self.pref
        # Zeit um: die nächste Leitung, die noch nicht dran war (zuerst die, die nach der jetzigen kommt)
        cur = self.pref or (self.tried[-1] if self.tried else good[0])
        order = good[good.index(cur) + 1:] + good[:good.index(cur) + 1] if cur in good else list(good)
        if cur and cur not in self.tried:
            self.tried.append(cur)
        nxt = next((n for n in order if n not in self.tried), None)
        if nxt is None:
            self.tried = []
            nxt = order[0]
        self.pref, self.bad_since = nxt, now
        self.switched += 1
        return self.pref


class Health:
    """Hält je Karte fest, ob sie gilt oder ausgefallen ist (Verzögerung gegen kurze Aussetzer)."""

    def __init__(self, fails_needed=FAILS_NEEDED):
        self.need = fails_needed
        self.state = {}

    def update(self, name, ok, now):
        s = self.state.setdefault(name, {"ok": True, "fails": 0, "since": now, "known": False})
        if ok:
            if not s["ok"] or not s["known"]:
                s["since"] = now
            s["ok"], s["fails"], s["known"] = True, 0, True
        else:
            s["fails"] += 1
            if s["ok"] and s["fails"] >= self.need:
                s["ok"], s["since"] = False, now
            s["known"] = True
        return s["ok"]

    def forget(self, keep):
        for n in [n for n in self.state if n not in keep]:
            del self.state[n]


def plan_routes(managed, current):
    """managed: {Karte: {gw, idx, ok}}; current: Standardrouten (parse_default_routes). -> Liste von ip-Befehlen (Argumentlisten)

    Gewollt je verwalteter Karte mit Router: genau eine Standardroute mit Metrik 100+idx (ok) oder 900+idx (ausgefallen), nirgends eine ohne Metrik."""
    cmds = []
    want = {}
    for name, m in managed.items():
        if not m.get("gw") or not m.get("idx"):
            continue
        want[name] = (m["gw"], (PREF_BASE if m["ok"] and m.get("pref") else GOOD_BASE if m["ok"] else BAD_BASE) + m["idx"])
    for name, (gw, metric) in want.items():
        have = [r for r in current if r["dev"] == name]
        if not any(r["via"] == gw and r["metric"] == metric for r in have):
            cmds.append(["ip", "route", "replace", "default", "via", gw, "dev", name, "metric", str(metric)])
    for name, (gw, metric) in want.items():
        idx = managed[name]["idx"]
        for r in [r for r in current if r["dev"] == name]:
            if (r["via"], r["metric"]) == (gw, metric):
                continue
            if r["metric"] not in (0, PREF_BASE + idx, GOOD_BASE + idx, BAD_BASE + idx):
                continue                                    # fremde Route (zum Beispiel vom NetworkManager): nicht anfassen
            if r["metric"] == 0:
                cmds.append(["ip", "route", "del", "default", "via", r["via"], "dev", name] if r["via"] else ["ip", "route", "del", "default", "dev", name])
            else:
                cmds.append(["ip", "route", "del", "default"] + (["via", r["via"]] if r["via"] else []) + ["dev", name, "metric", str(r["metric"])])
    return cmds


def plan_table(iface, ups, gw, idx, tables, table_text, rule_text):
    """Tabelle der Karte für die Sendewege prüfen. ups: ihre Adressen im Netz des Routers (die erste ist die per DHCP). -> (ip-Befehle, Grund oder None)

    Jede dieser Adressen braucht eine Regel "from <Adresse> lookup <Karte>": Hängt neben der DHCP-Adresse eine feste Zusatzadresse im selben Netz an der Karte,
    kann die feste die erste sein; bindet ein Programm (srtla) an sie, brauchte sie sonst den Weg der ersten Karte."""
    cmds, why = [], None
    if iface not in tables:
        return cmds, "tabelle_unbekannt"
    if not ups:
        return cmds, None
    src = ups[0]
    net = str(ipaddress.ip_network(f"{src['ip']}/{src['plen']}", strict=False))
    defs = parse_default_routes(table_text)
    if not any(r["dev"] == iface and r["via"] == gw for r in defs) or len(defs) != 1:
        cmds.append(["ip", "route", "replace", "default", "via", gw, "dev", iface, "table", iface])
        why = "tabelle_falsch"
    if not re.search("^" + re.escape(net) + r"(?:\s|$)", table_text, re.M):
        cmds.append(["ip", "route", "replace", net, "dev", iface, "proto", "kernel", "scope", "link", "src", src["ip"], "table", iface])
        why = why or "tabelle_falsch"
    rules = re.findall(r"^(\d+):\s+from (\S+) lookup (\S+)", rule_text, re.M)
    mine = [(p, f) for p, f, t in rules if t == iface]
    have = {f.split("/")[0] for p, f in mine}
    want = {a["ip"] for a in ups}
    if mine:
        pri = mine[0][0]
    elif any(5000 <= int(p) < 6000 for p, f, t in rules):
        pri = str(5200 + idx)
    else:
        pri = None
    for a in ups:
        if a["ip"] not in have:
            cmds.append(["ip", "rule", "add", "from", a["ip"], "lookup", iface] + (["priority", pri] if pri else []))
            why = why or "regel_fehlt"
    for p, f in mine:
        if f.split("/")[0] not in want:
            cmds.append(["ip", "rule", "del", "from", f, "lookup", iface, "priority", p])
    return cmds, why


# ------------------------------------------------------------------ Ablauf

class Watcher:
    def __init__(self, dry=False):
        self.dry = dry
        self.health = Health()
        self.started = time.time()
        self.last = {}
        self.rotor = Rotor()
        self.mute = Mute()
        self.muted_logged = set()
        self.ts_next = 0
        self.ts = None

    def snapshot(self):
        rc, out = run(["ip", "-4", "-o", "addr", "show", "scope", "global"])
        addrs = parse_addrs(out) if rc == 0 else {}
        rc, dtext = run(["ip", "-4", "route", "show", "default"])
        defaults = parse_default_routes(dtext) if rc == 0 else []
        return addrs, defaults

    def cycle(self):
        now = int(time.time())
        addrs, defaults = self.snapshot()
        tables = table_names()
        rc, rule_text = run(["ip", "-4", "rule", "show"])
        info = {}
        managed = {}
        probe_ips = {}
        # 1. je Karte Router, Adressen und Tabelle bestimmen; die Tabelle für die Sendewege bei Bedarf richten
        for name in sorted(addrs):
            tdefs = []
            rc, ttext = run(["ip", "-4", "route", "show", "table", name]) if name in tables else (1, "")
            if rc == 0:
                tdefs = parse_default_routes(ttext)
            gw = gateway_for(name, addrs[name], defaults, tdefs)
            ups = uplink_addrs(addrs[name], gw)
            idx = ifindex(name)
            pip = (ups or addrs[name])[0]["ip"]
            entry = {"ip": pip, "ips": [a["ip"] for a in (ups or addrs[name])], "gw": gw, "idx": idx, "managed": bool(MANAGED_RE.match(name)), "ok": None, "err": None,
                     "text": None, "rtt_ms": None, "table": None, "since": now}
            info[name] = entry
            probe_ips[name] = (pip, 0)
            if gw and name in tables and entry["managed"]:
                cmds, why = plan_table(name, ups, gw, idx, tables, ttext if rc == 0 else "", rule_text)
                entry["table"] = why or "ok"
                self.apply(cmds, f"Tabelle {name}")
            elif name in tables:
                entry["table"] = "ok"
            else:
                entry["table"] = "tabelle_unbekannt" if entry["managed"] else None
        # 2. Internet je Karte prüfen (alle gleichzeitig), danach den Weg jeder Adresse
        res = probe_all(probe_ips) if probe_ips else {}
        for name, entry in info.items():
            p = res.get(name) or {"ok": False, "err": "zeitueberschreitung", "rtt_ms": None}
            ok = self.health.update(name, bool(p["ok"]), now)
            entry["ok"], entry["rtt_ms"] = ok, p.get("rtt_ms")
            entry["since"] = self.health.state[name]["since"]
            if not entry["gw"]:
                entry["err"], entry["text"] = "kein_router", ERR_TEXT["kein_router"]
            elif not p["ok"]:
                entry["err"], entry["text"] = p["err"], ERR_TEXT.get(p["err"], p["err"])
            devs = {}
            for ip in entry["ips"]:
                rc, gtext = run(["ip", "-4", "route", "get", ROUTE_TEST, "from", ip], 4)
                devs[ip] = route_dev(gtext) if rc == 0 else None
            entry["route_dev"] = devs.get(entry["ip"])
            wrong = [ip for ip, dev in devs.items() if dev and dev != name]
            if wrong and entry["gw"]:
                entry["err"], entry["text"] = "falsche_karte", ERR_TEXT["falsche_karte"]
            if entry["managed"] and entry["gw"]:
                managed[name] = {"gw": entry["gw"], "idx": entry["idx"], "ok": entry["ok"]}
        self.health.forget(set(info))
        # Tailscale ohne Verbindung trotz Internet: die nächste Leitung ausprobieren
        if time.time() >= self.ts_next:
            self.ts_next = time.time() + 5
            rc, tout = run(["tailscale", "status", "--json"], 6)
            self.ts = tailscale_state(tout) if rc == 0 else None
        by_ip = {ip: n for n, e in info.items() for ip in e["ips"]}
        try:
            with open(LINKS_FILE) as f:
                fresh = time.time() - os.fstat(f.fileno()).st_mtime < 20
                evidence = srtla_evidence(f.read(), by_ip) if fresh else {}
        except OSError:
            evidence = {}
        muted = self.mute.update(time.time(), evidence, {n for n, m in managed.items() if m["ok"]})
        for n, m in managed.items():
            m["ok"] = m["ok"] and n not in muted         # Metrik 900+: die Box selbst nimmt eine Leitung, auf der der Server antwortet
            info[n]["srt"] = "stumm" if n in muted else (True if evidence.get(n) else None)
        for n in muted - self.muted_logged:
            log(f"{n}: Internet geht, aber der SRT-Server antwortet dort nicht; die Box nimmt für sich eine andere Leitung")
        self.muted_logged = set(muted)
        good = [n for n in sorted(managed, key=lambda n: managed[n]["idx"]) if managed[n]["ok"]]
        before = self.rotor.pref
        pref = self.rotor.update(time.time(), self.ts, good)
        if pref != before and pref:
            log(f"Tailscale ohne Verbindung: die Box versucht {pref}")
        for n, m in managed.items():
            m["pref"] = (n == pref)
        grace = time.time() - self.started < 20
        if not grace:
            cmds = plan_routes(managed, defaults)
            self.apply(cmds, "Routen der Box")
        for name, entry in info.items():
            before = self.last.get(name)
            if before is not None and before != entry["ok"]:
                if entry["ok"]:
                    log(f"{name}: wieder Internet")
                else:
                    ok_others = [n for n, e in info.items() if e["ok"] and n != name and e["managed"]]
                    log(f"{name}: ohne Internet ({entry['text']}); die Box nimmt " + (", ".join(ok_others) if ok_others else "keine andere Leitung (alle ausgefallen)"))
            self.last[name] = entry["ok"]
        for n in [n for n in self.last if n not in info]:
            del self.last[n]
        return {"time": now, "interval": INTERVAL, "ifaces": info, "dry": self.dry, "tailscale": {"online": self.ts, "preferred": self.rotor.pref}}

    def apply(self, cmds, what):
        for c in cmds:
            if self.dry:
                log(f"(nur gezeigt) {what}: {' '.join(c)}")
                continue
            rc, out = run(c, 8)
            if rc != 0:
                log(f"{what}: {' '.join(c)} -> {out.strip()[:160]}")
            else:
                log(f"{what}: {' '.join(c)}")


def write_status(data):
    try:
        os.makedirs(RUN, exist_ok=True)
        os.chmod(RUN, 0o755)
        tmp = STATUS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATUS)
    except OSError as e:
        log(f"Status nicht geschrieben: {e}")


def uninstall():
    rc, text = run(["ip", "-4", "route", "show", "default"])
    for r in parse_default_routes(text) if rc == 0 else []:
        if r["metric"] >= PREF_BASE and r["metric"] < GOOD_BASE + 800 or r["metric"] >= BAD_BASE and r["metric"] < BAD_BASE + 100:
            run(["ip", "route", "del", "default"] + (["via", r["via"]] if r["via"] else []) + ["dev", r["dev"], "metric", str(r["metric"])])
    try:
        os.remove(STATUS)
    except OSError:
        pass


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "watch"
    if mode == "uninstall":
        uninstall()
        return 0
    w = Watcher(dry=(mode == "dry"))
    if mode in ("once", "dry"):
        w.started = 0
        data = w.cycle()
        if mode == "once":
            write_status(data)
        print(json.dumps(data, indent=1))
        return 0
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    while not stop.is_set():
        t0 = time.time()
        try:
            write_status(w.cycle())
        except Exception as e:
            log(f"Fehler im Durchlauf: {type(e).__name__}: {e}")
        stop.wait(max(1.0, INTERVAL - (time.time() - t0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
