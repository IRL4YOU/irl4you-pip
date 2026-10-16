#!/usr/bin/env python3
"""Root-Helfer für WLAN-Verbindungen (läuft nur über pipbox-wifi.path).

Liest aus der Auslösedatei eine feste Aktion (scan, connect, forget, disconnect, hotspot_start, hotspot_stop, hotspot_save, export_wifi, import_wifi) mit streng geprüften
Werten (WLAN-Karte, Netzname, Passwort) und führt sie mit nmcli aus. Das Passwort eines WLANs, mit dem sich die Box verbindet, wird nie
als Befehlsargument übergeben (nur über stdin an nmcli) und nie ins Protokoll geschrieben; die Auslösedatei wird vor der Ausführung gelöscht.
Die Karte des Kameranetzes (camera-net.json) und Karten ohne WLAN werden abgelehnt.

Sichern und Einspielen der gespeicherten Netze (Issue #20): "export_wifi" liest die Profile und, nur auf Wunsch, ihre Passwörter und legt die Liste für die
Oberfläche in wifi-export.json (Benutzer pipbox, 0600, wird dort sofort gelesen und gelöscht). "import_wifi" legt Profile aus einer Liste an. Einzige Ausnahme von
"Passwörter nie als Befehlsargument": nmcli kennt zum Anlegen eines Profils ohne Verbindung nur die Befehlszeile; das Passwort steht dort für den Bruchteil einer
Sekunde (sichtbar für root und den Dienstbenutzer) und wird in keiner Meldung wiederholt.

Hotspot: Ein Stick wird mit einem NetworkManager-Profil "pipbox-hotspot-<Karte>" zum Zugangspunkt (WPA2, Adressvergabe und Weitergabe durch
NetworkManager, "shared"). Das Passwort eines Hotspots ist zum Weitergeben an Kameras und Handys gedacht und liegt deshalb in
hotspot.json (Benutzer pipbox, 0600), damit die Oberfläche es anzeigen und die Kamera-Anbindung es lesen kann.
"""
import json
import os
import pwd
import stat
import re
import subprocess
import sys
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/wifi-request"
RUN = "/run/pipbox-wifi"
STATUS = f"{RUN}/status.json"
IFACE_RE = re.compile(r"^[a-z][a-z0-9]{1,14}$")
ACTIONS = ("scan", "connect", "forget", "disconnect", "hotspot_start", "hotspot_stop", "hotspot_save", "hotspot_delete", "export_wifi", "import_wifi")
EXPORT_FILE = f"{STATE}/wifi-export.json"
MARK_RE = re.compile(r"^[0-9a-f]{16}$")
HS_PREFIX = "pipbox-hotspot-"
SYS_NET = "/sys/class/net"
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
HOTSPOT_FILE = f"{STATE}/hotspot.json"
HS_BANDS = {"bg": tuple(range(1, 14)), "a": (36, 40, 44, 48)}       # erlaubte Kanäle (0 = automatisch); 5 GHz nur ohne Radarpflicht (DFS)


def nm(*args, stdin=None, timeout=60):
    return subprocess.run(["nmcli", *args], input=stdin, capture_output=True, text=True, timeout=timeout)


def split_terse(line):
    """nmcli -t trennt mit ':' und maskiert ':' und '\\' im Text mit '\\'."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def write_status(**kw):
    os.makedirs(RUN, exist_ok=True)
    try:
        with open(STATUS) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.update(kw)
    s["time"] = int(time.time())
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATUS)


def wifi_devices():
    r = nm("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev")
    devs = []
    for line in r.stdout.splitlines():
        p = split_terse(line)
        if len(p) >= 4 and p[1] == "wifi":
            devs.append({"iface": p[0], "state": p[2], "connection": p[3]})
    return devs


def connection_names():
    r = nm("-t", "-f", "NAME,TYPE", "con", "show")
    return [p for p in (split_terse(l) for l in r.stdout.splitlines()) if len(p) >= 2 and p[1] == "802-11-wireless"]


def hwaddr(iface):
    """Hardware-Adresse einer Karte (aus /sys), klein geschrieben, oder "" wenn es sie nicht gibt."""
    try:
        with open(f"{SYS_NET}/{iface}/address") as f:
            mac = f.read().strip().lower()
    except (OSError, ValueError):
        return ""
    return mac if MAC_RE.match(mac) and mac != "00:00:00:00:00:00" else ""


def bind_to_card(name, iface):
    """Ein Profil an die Karte binden, und zwar über ihre Hardware-Adresse statt über den Namen (wlan0, wlan1 ...). Die Namen vergibt der Kernel in der Reihenfolge, in der
    die Geräte beim Start erscheinen, und sie wechseln, sobald ein Stick steckt oder fehlt: Ein an den Namen gebundenes Profil landete dann auf der falschen Karte (die
    eingebaute Karte im Handy-Hotspot statt im Kameranetz, 11. Oktober 2026). Gibt True zurück, wenn die Adresse eingetragen wurde (sonst bleibt es beim Namen)."""
    mac = hwaddr(iface)
    if mac:
        r = nm("con", "modify", "id", name, "connection.interface-name", "", "802-11-wireless.mac-address", mac)
        if r.returncode == 0:
            return True
    nm("con", "modify", "id", name, "connection.interface-name", iface)
    return False


def do_migrate():
    """Beim Update einmal: Profile, die an einen Kartennamen gebunden sind, werden an die Karte gebunden, die den Namen gerade trägt. Unberührt bleiben Profile mit
    Hardware-Adresse oder für alle Karten, die Hotspot-Profile dieser Box, das Kameranetz und Namen, die es gerade nicht gibt."""
    bound, skipped = [], 0
    for name in saved_wifi():
        r = nm("-g", "connection.type,connection.interface-name,802-11-wireless.mac-address", "con", "show", "id", name, timeout=15)
        v = [x.strip() for x in r.stdout.splitlines()] + ["", "", ""]
        ctype, ifn, mac = v[0], v[1], v[2]
        if r.returncode != 0 or ctype != "802-11-wireless" or not ifn or ifn == "*" or mac:
            continue
        try:
            check_not_camera_profile(name)
        except ValueError:
            continue
        if ifn == camera_iface() or not hwaddr(ifn):
            skipped += 1
            continue
        if bind_to_card(name, ifn):
            bound.append(f"{name} ({ifn})")
        else:
            skipped += 1
    hs_bound = migrate_hotspots()
    print(f"pipbox-wifi: {len(bound)} Profile an die Hardware-Adresse der Karte gebunden" + (": " + ", ".join(bound) if bound else "") +
          (f"; {skipped} unverändert (Karte nicht da oder Kameranetz)" if skipped else "") +
          (f"; Hotspots: {', '.join(hs_bound)}" if hs_bound else ""))
    return 0


def migrate_hotspots():
    """Hotspot-Profile dieser Box, die an einen Kartennamen gebunden sind, an die Hardware-Adresse binden. Welche Karte gemeint ist, sagt hotspot.json
    (Netzname gleich, die Schlüssel dort folgen der Karte, wenn die Namen wechseln); gibt es keinen Eintrag, ist es die Karte, die den Namen jetzt trägt.
    Das Profil wird nach der Karte benannt, damit es beim Beenden und Anzeigen die richtige Karte trifft. Gibt die Liste "Profil -> Karte" zurück."""
    done = []
    hs = hs_load()
    names = [p[0] for p in connection_names()]
    for name in [n for n in names if n.startswith(HS_PREFIX)]:
        p = profile_props(name, ["connection.interface-name", "802-11-wireless.mac-address", "802-11-wireless.ssid"])
        if p.get("802-11-wireless.mac-address"):
            continue
        card = next((i for i, h in hs.items() if isinstance(h, dict) and h.get("ssid") and h.get("ssid") == p.get("802-11-wireless.ssid") and hwaddr(i)), "")
        card = card or p.get("connection.interface-name", "")
        if not card or not hwaddr(card):
            continue
        if bind_to_card(name, card):
            new = hs_name(card)
            if new != name and new not in names:
                nm("con", "modify", "id", name, "connection.id", new)
                names.append(new)
                name = new
            done.append(f"{name} -> {card}")
    return done


def saved_wifi():
    """Gespeicherte WLAN-Netze zum Verbinden (ohne die Hotspot-Profile dieser Box)."""
    return sorted(p[0] for p in connection_names() if not p[0].startswith(HS_PREFIX))


def hs_name(iface):
    return HS_PREFIX + iface


def hotspot_active(iface):
    """Läuft auf dieser Karte gerade ein Hotspot dieser Box? Der Name des Profils trägt den Kartennamen von damals (wlan1 ...), und der wechselt;
    darum zählt jedes Profil "pipbox-hotspot-…", das auf dieser Karte verbunden ist."""
    return any(d["iface"] == iface and str(d["connection"]).startswith(HS_PREFIX) and d["state"] == "connected" for d in wifi_devices())


def hs_profile(iface):
    """Name des Hotspot-Profils dieser Karte: das, was auf ihr läuft, sonst das nach ihrem Namen benannte. None, wenn keines da ist."""
    for d in wifi_devices():
        if d["iface"] == iface and str(d["connection"]).startswith(HS_PREFIX) and d["state"] == "connected":
            return d["connection"]
    names = [p[0] for p in connection_names()]
    mine = hwaddr(iface)
    if mine:
        for n in names:
            if n.startswith(HS_PREFIX) and profile_props(n, ["802-11-wireless.mac-address"]).get("802-11-wireless.mac-address", "").lower() == mine:
                return n
    return hs_name(iface) if hs_name(iface) in names else None


def wifi_caps(iface):
    """Was die Karte kann (NetworkManager): Zugangspunkt, 2,4 und 5 GHz."""
    r = nm("-g", "WIFI-PROPERTIES.AP,WIFI-PROPERTIES.2GHZ,WIFI-PROPERTIES.5GHZ", "dev", "show", iface, timeout=15)
    v = [x.strip().lower() == "yes" for x in r.stdout.splitlines()]
    v += [False] * (3 - len(v))
    return {"ap": v[0], "2ghz": v[1], "5ghz": v[2]}


def camera_iface():
    try:
        with open(f"{STATE}/camera-net.json") as f:
            return str(json.load(f).get("iface") or "")
    except (OSError, ValueError):
        return ""


def check_iface(iface):
    if not isinstance(iface, str) or not IFACE_RE.match(iface):
        raise ValueError("WLAN-Karte ungültig")
    if iface not in [d["iface"] for d in wifi_devices()]:
        raise ValueError("Das ist keine WLAN-Karte")
    if iface == camera_iface():
        raise ValueError("Diese Karte ist das Kameranetz und wird nicht verändert")


def check_client_iface(iface):
    """Wie check_iface, aber für Aktionen als Client (suchen, verbinden, trennen): Läuft auf der Karte ein Hotspot, geht das nicht."""
    check_iface(iface)
    if hotspot_active(iface):
        raise ValueError("Auf dieser Karte läuft ein Hotspot. Bitte zuerst den Hotspot beenden.")


def check_not_hotspot_name(ssid):
    if isinstance(ssid, str) and ssid.startswith(HS_PREFIX):
        raise ValueError("Dieser Netzname ist für den Hotspot der Box reserviert")


def check_not_camera_profile(ssid):
    """Das Profil, mit dem die Kamera-Karte gerade verbunden ist, wird weder ersetzt noch gelöscht."""
    cam = camera_iface()
    if cam and any(d["iface"] == cam and d["connection"] == ssid for d in wifi_devices()):
        raise ValueError("Dieses WLAN gehört zum Kameranetz und wird nicht verändert")


def check_ssid(ssid):
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or any(ord(c) < 32 or ord(c) == 127 for c in ssid):
        raise ValueError("Netzname ungültig (1 bis 32 Zeichen)")


def check_password(pw):
    if pw == "":
        return
    if not isinstance(pw, str) or not (8 <= len(pw) <= 63 and all(32 <= ord(c) < 127 for c in pw)
                                         or re.fullmatch(r"[0-9a-fA-F]{64}", pw or "")):
        raise ValueError("Passwort: 8 bis 63 Zeichen (nur ASCII) oder leer bei offenem Netz")


def _add_net(best, p):
    """Ein Netz (IN-USE, SSID, SIGNAL, SECURITY) in die Liste {SSID: Netz} aufnehmen: das stärkste Signal je Name gilt, "verbunden" bleibt erhalten."""
    if len(p) < 4 or not p[1]:
        return
    sig = int(p[2]) if p[2].isdigit() else 0
    cur = best.get(p[1])
    if cur is None or sig > cur["signal"] or p[0] == "*":
        best[p[1]] = {"ssid": p[1], "signal": sig, "security": p[3] or "offen", "in_use": p[0] == "*" or bool(cur and cur["in_use"])}


def read_nets(iface):
    """Die Liste der gefundenen Netze einer Karte, wie NetworkManager sie gerade kennt: {SSID: Netz} (ohne neuen Suchlauf). Sie wird auf zwei Wegen
    gelesen und vereinigt: mit "ifname" und, wie es die Original-Oberfläche der BELABOX macht, ohne "ifname" für alle Karten, nach der Spalte DEVICE
    auf diese Karte beschränkt (manche Sticks melden ihre Netze auf einem der beiden Wege nicht)."""
    best = {}
    r = nm("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "ifname", iface, "--rescan", "no", timeout=15)
    for line in r.stdout.splitlines():
        _add_net(best, split_terse(line))
    try:
        r = nm("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,DEVICE", "dev", "wifi", "list", "--rescan", "no", timeout=15)
        for line in r.stdout.splitlines():
            p = split_terse(line)
            if len(p) >= 5 and p[4] == iface:
                _add_net(best, p[:4])
    except subprocess.TimeoutExpired:
        pass
    return best


SCAN_WAIT = 25        # so lange wird auf die Ergebnisse eines Suchlaufs gewartet (Sekunden); die Original-Oberfläche liest bis zu 20 s nach
SCAN_MIN = 8          # mindestens so lange lesen, auch wenn schon (ältere) Netze in der Liste stehen: der gerade angestoßene Suchlauf liefert erst später neue
SCAN_STABLE = 4       # nach dem letzten neuen Netz so lange noch abwarten, ob weitere dazukommen (Sekunden)
SCAN_POLL = 1.5       # Abstand der Abfragen (Sekunden)
SCAN_RESCAN_EVERY = 10   # bleibt die Liste leer, wird der Suchlauf alle so viele Sekunden noch einmal angestoßen (NetworkManager lehnt zu frühe ab: egal)


def rescan(iface, log=None):
    """Suchlauf anstoßen: wie die Original-Oberfläche für alle Karten (ohne ifname) und danach gezielt für diese Karte. Ein Fehler ("Suchlauf gerade
    nicht erlaubt") ist hier kein Problem, die Liste wird trotzdem gelesen; die Meldungen werden für die technischen Angaben gemerkt."""
    for args in (("dev", "wifi", "rescan"), ("dev", "wifi", "rescan", "ifname", iface)):
        try:
            r = nm(*args, timeout=20)
            if log is not None:
                log.append("%s: %s" % (" ".join(args), ((r.stderr or r.stdout).strip()[:140] or "Fehler") if r.returncode else "ok"))
        except subprocess.TimeoutExpired:
            if log is not None:
                log.append("%s: Zeitüberschreitung" % " ".join(args))


def sorted_nets(best):
    return sorted(best.values(), key=lambda n: (not n["in_use"], -n["signal"]))[:40]


DIAG_SKIP = {"GENERAL.DBUS-PATH", "GENERAL.UDI", "GENERAL.CON-UUID", "GENERAL.CON-PATH", "GENERAL.NM-TYPE", "GENERAL.PHYS-PORT-ID", "GENERAL.IS-SOFTWARE",
             "GENERAL.METERED", "GENERAL.IP4-CONNECTIVITY", "GENERAL.IP6-CONNECTIVITY", "GENERAL.PATH", "GENERAL.IP-IFACE", "CAPABILITIES.CARRIER-DETECT",
             "CAPABILITIES.SPEED", "CAPABILITIES.SRIOV", "CAPABILITIES.IS-SOFTWARE", "INTERFACE-FLAGS.PROMISC"}      # Rauschen: für die Fehlersuche nicht nötig
MAC_RE = re.compile(r"\b([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){2})(?:[:-][0-9A-Fa-f]{2}){3}\b")


def scan_diagnostics(iface, rescan_log=None):
    """Technische Angaben zur Karte für den Fall, dass kein Netz gefunden wird (erscheinen in der Oberfläche und im Protokoll-Download): Zustand der
    Karte laut NetworkManager, Funk (rfkill), Fähigkeiten, Zahl der Funkstationen insgesamt und Meldungen des Treibers. MAC-Adressen sind gekürzt."""
    lines = []

    def run(*cmd, timeout=10):
        try:
            r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, errors="replace")
            return (r.stdout or "") + (r.stderr if not r.stdout else "")
        except (OSError, subprocess.TimeoutExpired):
            return ""
    for l in run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev").splitlines():
        if l.startswith(iface + ":"):
            lines.append("Karte: " + l)
    for l in run("nmcli", "-t", "-f", "GENERAL,CAPABILITIES,INTERFACE-FLAGS,WIFI-PROPERTIES", "dev", "show", iface).splitlines():
        if l.split(":", 1)[0] not in DIAG_SKIP:
            lines.append(l)
    lines.append("Funk: " + " | ".join(x for x in run("nmcli", "-t", "radio", "all").splitlines() if x))
    for l in list(dict.fromkeys(rescan_log or []))[:6]:                # was NetworkManager zu den Suchläufen gesagt hat
        lines.append("Suchlauf angefordert: " + l)
    cfg = list(dict.fromkeys(l.strip() for l in run("NetworkManager", "--print-config").splitlines() if re.match(r"\s*wifi\.", l)))
    if cfg:
        lines.append("NetworkManager-Einstellungen: " + " | ".join(cfg[:8]))
    rf = run("rfkill", "list").strip()
    if rf:
        lines.append("rfkill: " + " ".join(rf.split())[:200])
    all_aps = run("nmcli", "-t", "-f", "SSID,DEVICE", "dev", "wifi", "list", "--rescan", "no").splitlines()
    mine = [l for l in all_aps if l.rsplit(":", 1)[-1] == iface]
    hidden = [l for l in mine if l.startswith(":") or l.split(":", 1)[0] == ""]
    lines.append("Funkstationen laut NetworkManager: %d insgesamt, %d auf %s (davon %d ohne Namen)" % (len(all_aps), len(mine), iface, len(hidden)))
    jl = [l for l in run("journalctl", "-u", "NetworkManager", "-u", "wpa_supplicant", "--no-pager", "-o", "short-iso", "-n", "800", timeout=15).splitlines()
          if re.search(r"\b%s\b" % re.escape(iface), l)][-10:]
    lines += ["NetworkManager: " + re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<IP>", re.sub(r"'[^']{1,48}'", "'…'", l.split(" ", 3)[-1] if l.count(" ") >= 3 else l)) for l in jl]
    # (Namen in Hochkommas und Adressen sind gekürzt, damit die Angaben ohne Nachdenken weitergegeben werden können)
    try:
        drv = os.path.basename(os.path.realpath(f"/sys/class/net/{iface}/device/driver"))
    except OSError:
        drv = ""
    key = re.compile(r"(?i)\b(%s|%s)\b" % (re.escape(iface), re.escape(drv))) if drv and drv != "driver" else re.compile(r"(?i)\b%s\b" % re.escape(iface))
    k = [l for l in run("dmesg").splitlines() if key.search(l)][-8:]
    if drv:
        lines.append("Treiber: " + drv)
    lines += ["Kernel: " + l for l in k]
    return [MAC_RE.sub(lambda m: m.group(1) + ":xx:xx:xx", l)[:220] for l in lines][:70]


def do_scan(iface):
    """Suchlauf auf der gewählten Karte, nach dem Vorbild der Original-Oberfläche der BELABOX: Suchlauf anstoßen, die Liste danach immer wieder lesen
    (die Ergebnisse trudeln ein; ein langsamer Stick mit beiden Bändern braucht bis zu 20 Sekunden) und die Netze schon während des Wartens anzeigen.
    Früher wurde nach 12 Sekunden aufgegeben und nur die Liste mit "ifname" gelesen: bei manchen Sticks (TP-Link, Issue #8) blieb sie leer. Bleibt sie
    auch jetzt leer, werden technische Angaben zur Karte mitgeliefert."""
    check_client_iface(iface)
    nm("radio", "wifi", "on")
    log = []
    rescan(iface, log)
    start = time.time()
    nets, last_new, next_rescan = {}, None, start + SCAN_RESCAN_EVERY
    while True:
        now = time.time()
        found = read_nets(iface)
        new = [k for k in found if k not in nets]
        for k, v in found.items():
            if k not in nets or v["signal"] > nets[k]["signal"] or v["in_use"]:
                nets[k] = dict(v, in_use=v["in_use"] or bool(nets.get(k, {}).get("in_use")))
        if new:
            last_new = now
            write_status(scan={"iface": iface, "nets": sorted_nets(nets), "scanned": int(now), "partial": True})     # schon anzeigen, die Suche läuft weiter
        if nets and last_new is not None and now - last_new >= SCAN_STABLE and now - start >= SCAN_MIN:
            break
        if now - start >= SCAN_WAIT:
            break
        if not nets and now >= next_rescan:
            rescan(iface, log)
            next_rescan = now + SCAN_RESCAN_EVERY
        time.sleep(SCAN_POLL)
    result = sorted_nets(nets)
    scan = {"iface": iface, "nets": result, "scanned": int(time.time())}
    if not result:
        scan["debug"] = scan_diagnostics(iface, log)
    write_status(scan=scan)
    if not result:
        return ("Keine Netze gefunden. Manche Sticks brauchen einen zweiten Suchlauf: bitte noch einmal „Netze suchen“ drücken. "
                "Bleibt es leer, stehen unten technische Angaben zur Karte.")
    return f"{len(result)} Netze gefunden"


def connect_saved(iface, ssid):
    """Ein gespeichertes Netz ohne neue Passworteingabe mit seinem gespeicherten Passwort auf der gewählten Karte einschalten. Früher wurde das
    Profil hier gelöscht und ohne Passwort neu versucht: das schlug mit "Password" fehl, und das gespeicherte Netz war danach weg."""
    bind_to_card(ssid, iface)                                                 # das Profil gilt auch für eine andere Karte, wenn man es dort wählt (an ihre Hardware-Adresse gebunden)
    r = nm("con", "up", "id", ssid, "ifname", iface, timeout=60)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()[:160] or "unbekannter Fehler"
        raise RuntimeError(f"Verbinden mit dem gespeicherten Netz „{ssid}“ fehlgeschlagen: {msg}. Hat sich das Passwort geändert, bitte neu eingeben.")
    nm("con", "modify", "id", ssid, "connection.autoconnect", "yes", "ipv4.route-metric", "600")
    return f"Mit „{ssid}“ verbunden (gespeichertes Netz)"


def do_connect(req):
    iface, ssid, pw = req.get("iface"), req.get("ssid"), req.get("password", "")
    check_client_iface(iface)
    check_ssid(ssid)
    check_not_hotspot_name(ssid)
    check_password(pw)
    hidden = req.get("hidden") is True
    if ssid in saved_wifi():
        check_not_camera_profile(ssid)
        if not pw and not hidden:
            return connect_saved(iface, ssid)
        nm("con", "delete", "id", ssid)             # neues Passwort eingegeben: altes Profil ersetzen
    args = ["--ask", "dev", "wifi", "connect", ssid, "ifname", iface]
    if hidden:
        args += ["hidden", "yes"]
    r = nm(*args, stdin=(pw + "\n") if pw else None, timeout=60)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().replace(pw, "***") if pw else (r.stderr or r.stdout).strip()
        raise RuntimeError("Verbinden fehlgeschlagen: " + (msg[:160] or "unbekannter Fehler"))
    bind_to_card(ssid, iface)                                                 # das neue Profil folgt der Karte, nicht ihrem Namen
    nm("con", "modify", "id", ssid, "connection.autoconnect", "yes", "ipv4.route-metric", "600")
    return f"Mit „{ssid}“ verbunden"


def do_forget(req):
    ssid = req.get("ssid")
    check_ssid(ssid)
    check_not_hotspot_name(ssid)
    if ssid not in saved_wifi():
        raise ValueError("Dieses WLAN ist nicht gespeichert")
    check_not_camera_profile(ssid)
    nm("con", "delete", "id", ssid)
    return f"„{ssid}“ vergessen"


def do_disconnect(req):
    check_client_iface(req.get("iface"))
    nm("dev", "disconnect", req["iface"])
    return "Getrennt"


# ------------------------------------------------------------------ Hotspot

def hs_load():
    """Gespeicherte Hotspot-Einstellungen {Karte: {ssid, password, band, channel}} (ohne Verweisen zu folgen)."""
    try:
        fd = os.open(HOTSPOT_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return {}
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return {}
        d = json.loads(os.read(fd, 65536).decode("utf-8", "replace"))
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}
    finally:
        os.close(fd)


def hs_store(data):
    """hotspot.json atomar schreiben, Besitzer pipbox, 0600. Der Ordner gehört dem Benutzer pipbox: nie einem untergeschobenen Verweis folgen
    (Temporärdatei vorher entfernen, mit O_EXCL | O_NOFOLLOW neu anlegen, Besitzer über den Dateideskriptor setzen)."""
    tmp = HOTSPOT_FILE + ".tmp"
    try:
        os.unlink(tmp)
    except OSError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            u = pwd.getpwnam("pipbox")
            os.fchown(fd, u.pw_uid, u.pw_gid)
        except (KeyError, PermissionError):
            pass
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, HOTSPOT_FILE)


def check_hotspot_ssid(ssid):
    check_ssid(ssid)
    if ssid != ssid.strip():
        raise ValueError("Netzname: keine Leerzeichen am Anfang oder Ende")
    check_not_hotspot_name(ssid)


def check_hotspot_password(pw):
    if not isinstance(pw, str) or not 8 <= len(pw) <= 63 or any(not 32 <= ord(c) < 127 for c in pw):
        raise ValueError("Passwort: 8 bis 63 Zeichen (nur ASCII)")


def hs_values(req, saved):
    """Name, Passwort, Band und Kanal eines Hotspots aus der Anfrage prüfen. Ein leeres Passwort heißt: das gespeicherte behalten."""
    ssid = req.get("ssid")
    check_hotspot_ssid(ssid)
    pw = req.get("password", "")
    if pw == "":
        pw = str(saved.get("password", ""))
    check_hotspot_password(pw)
    band = req.get("band", "bg")
    if band not in HS_BANDS:
        raise ValueError("Band ungültig (2,4 GHz oder 5 GHz)")
    ch = req.get("channel", 0)
    if isinstance(ch, bool) or not isinstance(ch, int) or (ch != 0 and ch not in HS_BANDS[band]):
        raise ValueError("Kanal ungültig für dieses Band")
    return ssid, pw, band, ch


def hs_check_caps(iface, band):
    caps = wifi_caps(iface)
    if not caps["ap"]:
        raise ValueError("Diese WLAN-Karte kann keinen Hotspot aufbauen (kein Zugangspunkt-Betrieb)")
    if band == "a" and not caps["5ghz"]:
        raise ValueError("Diese WLAN-Karte kann kein 5 GHz")
    if band == "bg" and not caps["2ghz"] and caps["5ghz"]:
        raise ValueError("Diese WLAN-Karte kann nur 5 GHz")


def do_hotspot_save(req):
    """Einstellungen des Hotspots speichern, ohne ihn zu starten (Knopf "Einstellen"). Läuft er gerade, geht das nicht: Die gespeicherten Werte
    gehören zum laufenden Profil (der Kamera-Dienst liest das Passwort daraus)."""
    iface = req.get("iface")
    check_iface(iface)
    if hotspot_active(iface):
        raise ValueError("Der Hotspot läuft: bitte zuerst ausschalten")
    saved = hs_load().get(iface)
    saved = saved if isinstance(saved, dict) else {}
    ssid, pw, band, ch = hs_values(req, saved)
    hs_check_caps(iface, band)
    data = hs_load()
    data[iface] = {"ssid": ssid, "password": pw, "band": band, "channel": ch}
    hs_store(data)
    return f"Hotspot-Einstellungen für {iface} gespeichert"


def do_hotspot_start(req):
    iface = req.get("iface")
    check_iface(iface)
    saved = hs_load().get(iface)
    saved = saved if isinstance(saved, dict) else {}
    ssid, pw, band, ch = hs_values(req, saved)
    hs_check_caps(iface, band)
    name = hs_name(iface)
    mine = hwaddr(iface)
    if name in [p[0] for p in connection_names()]:
        other = profile_props(name, ["802-11-wireless.mac-address"]).get("802-11-wireless.mac-address", "").lower()
        if other and mine and other != mine:                     # gleicher Name, aber das Profil gehört einer anderen Karte (die Namen wurden getauscht): nicht löschen
            name = f"{name}-{mine.replace(':', '')}"
        if name in [p[0] for p in connection_names()]:
            nm("con", "delete", "id", name)                      # Profil neu anlegen, damit nichts Altes (Kanal, Passwort) zurückbleibt
    args = ["con", "add", "type", "wifi", "ifname", iface, "con-name", name, "autoconnect", "yes", "ssid", ssid,
            "802-11-wireless.mode", "ap", "802-11-wireless.band", band]
    if ch:
        args += ["802-11-wireless.channel", str(ch)]
    # Das Passwort eines Hotspots ist zum Weitergeben gedacht (Kamera, Handy); der Befehl läuft nur kurz als root.
    args += ["ipv4.method", "shared", "ipv6.method", "ignore", "connection.autoconnect-priority", "100",
             "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.proto", "rsn", "wifi-sec.pairwise", "ccmp", "wifi-sec.group", "ccmp", "wifi-sec.psk", pw]
    r = nm(*args, timeout=30)
    if r.returncode == 0:
        bind_to_card(name, iface)                                # an die Hardware-Adresse binden: Die Namen wlan0, wlan1 … wechseln, der Hotspot bleibt auf seinem Stick
    if r.returncode != 0:
        raise RuntimeError("Hotspot konnte nicht angelegt werden: " + ((r.stderr or r.stdout).strip().replace(pw, "***")[:160] or "unbekannter Fehler"))
    r = nm("con", "up", "id", name, "ifname", iface, timeout=60)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().replace(pw, "***")[:160] or "unbekannter Fehler"
        nm("con", "delete", "id", name)
        try:
            nm("dev", "connect", iface, timeout=30)                  # das vorherige Netz der Karte wieder aufnehmen (soweit gespeichert)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise RuntimeError("Hotspot konnte nicht gestartet werden: " + msg)
    data = hs_load()
    data[iface] = {"ssid": ssid, "password": pw, "band": band, "channel": ch}
    hs_store(data)
    return f"Hotspot „{ssid}“ läuft auf {iface} ({'5' if band == 'a' else '2,4'} GHz, Kanal {ch or 'automatisch'})"


def do_hotspot_stop(req):
    iface = req.get("iface")
    check_iface(iface)
    name = hs_profile(iface)
    if not name:
        raise ValueError("Auf dieser Karte ist kein Hotspot eingerichtet")
    nm("con", "modify", "id", name, "connection.autoconnect", "no")      # beendet bleibt beendet, auch nach einem Neustart der Box
    nm("con", "down", "id", name)                                          # ist er gar nicht aktiv, meldet nmcli einen Fehler: egal
    return "Hotspot beendet. Die Karte verbindet sich wieder mit einem gespeicherten Netz, wenn eines in Reichweite ist."


def do_hotspot_delete(req):
    """Den Hotspot einer Karte ganz entfernen: Profil in NetworkManager und gespeicherte Einstellungen (Name, Passwort, Band, Kanal). Nur bei beendetem Hotspot.
    Danach kann ein Hotspot auf einer anderen Karte eingerichtet werden; auf dieser Karte beginnt man wieder bei "Einstellen"."""
    iface = req.get("iface")
    check_iface(iface)
    if hotspot_active(iface):
        raise ValueError("Der Hotspot läuft: bitte zuerst ausschalten, dann löschen")
    name = hs_profile(iface)
    data = hs_load()
    if not name and iface not in data:
        raise ValueError("Auf dieser Karte ist kein Hotspot eingerichtet")
    if name:
        nm("con", "delete", "id", name)
    if iface in data:
        del data[iface]
        hs_store(data)
    return f"Hotspot auf {iface} gelöscht"


# ------------------------------------------------------------------ Gespeicherte Netze sichern und einspielen (Issue #20)

def unescape_terse(v):
    """nmcli -t maskiert ':' und '\\' im Wert mit '\\'."""
    out, esc = "", False
    for ch in v:
        if esc:
            out += ch
            esc = False
        elif ch == "\\":
            esc = True
        else:
            out += ch
    return out


def profile_props(name, fields, secrets=False):
    """Eigenschaften eines Profils {Feld: Wert} (die Passwörter nur mit secrets=True)."""
    args = (["-s"] if secrets else []) + ["-t", "-f", ",".join(fields), "con", "show", "id", name]
    r = nm(*args, timeout=20)
    out = {}
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            k, _, v = line.partition(":")
            if k in fields:
                out[k] = unescape_terse(v)
    return out


def read_saved_profile(name, secrets):
    """Ein gespeichertes WLAN-Profil lesen: ({ssid, hidden, open, security, password}, None) oder (None, Grund, Name des Netzes)."""
    p = profile_props(name, ["802-11-wireless.ssid", "802-11-wireless.mode", "802-11-wireless.hidden", "802-11-wireless-security.key-mgmt",
                             "802-11-wireless-security.psk-flags"])
    ssid = p.get("802-11-wireless.ssid") or name
    if p.get("802-11-wireless.mode", "infrastructure") not in ("infrastructure", ""):
        return None, "kein Client-Profil", ssid
    km = p.get("802-11-wireless-security.key-mgmt", "")
    if km not in ("", "wpa-psk", "sae"):
        return None, "Unternehmens-WLAN oder WEP", ssid
    try:
        check_ssid(ssid)
    except ValueError:
        return None, "Netzname ungültig", ssid
    net = {"ssid": ssid, "hidden": p.get("802-11-wireless.hidden", "no").lower() == "yes", "open": km == "", "security": km or "none", "password": ""}
    if km:
        m = re.match(r"\s*(\d+)", p.get("802-11-wireless-security.psk-flags", "0") or "0")
        if m and int(m.group(1)) & 3:                       # 1 = nur für den Benutzer im Programm, 2 = nicht gespeichert: das Passwort liegt nicht im Profil
            return None, "Passwort nicht gespeichert", ssid
        if secrets:                                          # das Passwort selbst nur auf ausdrücklichen Wunsch lesen
            pw = profile_props(name, ["802-11-wireless-security.psk"], secrets=True).get("802-11-wireless-security.psk", "")
            if not pw:
                return None, "Passwort nicht gespeichert", ssid
            try:
                check_password(pw)
            except ValueError:
                return None, "Passwort nicht übertragbar", ssid
            net["password"] = pw
    return net, None, ssid


def write_export(data):
    """Die Liste für die Oberfläche ablegen: Besitzer pipbox, 0600, nie einem untergeschobenen Verweis folgen (der Ordner gehört dem Benutzer pipbox)."""
    for path in (EXPORT_FILE + ".tmp", EXPORT_FILE):
        try:
            os.unlink(path)
        except OSError:
            pass
    tmp = EXPORT_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            u = pwd.getpwnam("pipbox")
            os.fchown(fd, u.pw_uid, u.pw_gid)
        except (KeyError, PermissionError):
            pass
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, EXPORT_FILE)


def do_export_wifi(req):
    secrets = req.get("secrets") is True
    nets, skipped, seen = [], [], set()
    for name in saved_wifi():
        net, why, ssid = read_saved_profile(name, secrets)
        if net is None:
            skipped.append({"ssid": ssid, "why": why})
        elif net["ssid"] not in seen:
            seen.add(net["ssid"])
            nets.append(net)
    write_export({"networks": nets, "skipped": skipped})
    return f"{len(nets)} WLAN-Netze gelesen" + (f", {len(skipped)} nicht übertragbar" if skipped else "")


def do_import_wifi(req):
    nets = req.get("networks")
    if not isinstance(nets, list) or not 1 <= len(nets) <= 50:
        raise ValueError("Liste der Netze ungültig (1 bis 50)")
    clean = []
    for n in nets:
        if not isinstance(n, dict):
            raise ValueError("Liste der Netze ungültig")
        ssid, pw = n.get("ssid"), n.get("password", "")
        check_ssid(ssid)
        check_not_hotspot_name(ssid)
        check_password(pw)
        sec = n.get("security", "wpa-psk")
        if sec not in ("wpa-psk", "sae", "none"):
            raise ValueError("Sicherheitsart ungültig")
        open_net = n.get("open") is True or sec == "none"
        if open_net and pw:
            raise ValueError("Ein offenes Netz hat kein Passwort")
        if not open_net and not pw:
            raise ValueError("Ein gesichertes Netz braucht ein Passwort")
        clean.append((ssid, pw, "none" if open_net else sec, n.get("hidden") is True))
    added, skipped = 0, []
    for ssid, pw, sec, hidden in clean:
        try:
            if ssid in saved_wifi():
                check_not_camera_profile(ssid)
                nm("con", "delete", "id", ssid)                  # gleichnamiges Profil wird ersetzt
            args = ["con", "add", "type", "wifi", "con-name", ssid, "ifname", "*", "ssid", ssid,
                    "connection.autoconnect", "yes", "ipv4.route-metric", "600"]
            if hidden:
                args += ["802-11-wireless.hidden", "yes"]
            if sec != "none":
                args += ["wifi-sec.key-mgmt", sec, "wifi-sec.psk", pw]
            r = nm(*args, timeout=30)
            if r.returncode != 0:
                msg = ((r.stderr or r.stdout).strip().replace(pw, "***") if pw else (r.stderr or r.stdout).strip())[:100]
                raise RuntimeError(msg or "nmcli meldet einen Fehler")
            added += 1
        except (ValueError, RuntimeError) as e:
            skipped.append(f"„{ssid}“ ({str(e)[:80]})")
    if not added and skipped:
        raise RuntimeError("Kein Netz eingespielt: " + "; ".join(skipped[:5]))
    return f"{added} WLAN-Netze eingespielt" + ((", ausgelassen: " + "; ".join(skipped[:5])) if skipped else "")


def read_req(path, limit=4096):
    """Anfragedatei im Ordner des Benutzers pipbox lesen, ohne Verweisen (Symlinks) zu folgen und nur bis zur Höchstgröße."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "migrate":              # nur von install.sh beim Update (als root), keine Eingaben von außen
        return do_migrate()
    try:
        req = json.loads(read_req(REQ, 65536))
    except (OSError, ValueError):
        req = None
    try:
        os.remove(REQ)                              # Passwort sofort von der Platte nehmen
    except OSError:
        pass
    if not isinstance(req, dict) or req.get("action") not in ACTIONS:
        write_status(state="error", message="Ungültige Anfrage")
        return 1
    action = req["action"]
    mark = req.get("mark") if isinstance(req.get("mark"), str) and MARK_RE.match(req["mark"]) else ""
    write_status(state="working", message="Wird ausgeführt …", action=action, mark=mark)
    try:
        if action == "scan":
            msg = do_scan(req.get("iface"))
        elif action == "connect":
            msg = do_connect(req)
        elif action == "forget":
            msg = do_forget(req)
        elif action == "hotspot_start":
            msg = do_hotspot_start(req)
        elif action == "hotspot_stop":
            msg = do_hotspot_stop(req)
        elif action == "hotspot_save":
            msg = do_hotspot_save(req)
        elif action == "hotspot_delete":
            msg = do_hotspot_delete(req)
        elif action == "export_wifi":
            msg = do_export_wifi(req)
        elif action == "import_wifi":
            msg = do_import_wifi(req)
        else:
            msg = do_disconnect(req)
        write_status(state="done", message=msg, saved=saved_wifi())
    except (ValueError, RuntimeError) as e:
        write_status(state="error", message=str(e), saved=saved_wifi())
    except (OSError, subprocess.TimeoutExpired) as e:
        write_status(state="error", message="Fehler: " + type(e).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
