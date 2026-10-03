#!/usr/bin/env python3
"""Root-Helfer für WLAN-Verbindungen (läuft nur über pipbox-wifi.path).

Liest aus der Auslösedatei eine feste Aktion (scan, connect, forget, disconnect) mit streng geprüften Werten
(WLAN-Karte, Netzname, Passwort) und führt sie mit nmcli aus. Das Passwort wird nie als Befehlsargument übergeben
(nur über stdin an nmcli) und nie ins Protokoll geschrieben; die Auslösedatei wird vor der Ausführung gelöscht.
Die Karte des Kameranetzes (camera-net.json) und Karten ohne WLAN werden abgelehnt.
"""
import json
import os
import re
import subprocess
import sys
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/wifi-request"
RUN = "/run/pipbox-wifi"
STATUS = f"{RUN}/status.json"
IFACE_RE = re.compile(r"^[a-z][a-z0-9]{1,14}$")
ACTIONS = ("scan", "connect", "forget", "disconnect")


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


def saved_wifi():
    r = nm("-t", "-f", "NAME,TYPE", "con", "show")
    return sorted(p[0] for p in (split_terse(l) for l in r.stdout.splitlines())
                  if len(p) >= 2 and p[1] == "802-11-wireless")


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


def check_ssid(ssid):
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or any(ord(c) < 32 or ord(c) == 127 for c in ssid):
        raise ValueError("Netzname ungültig (1 bis 32 Zeichen)")


def check_password(pw):
    if pw == "":
        return
    if not isinstance(pw, str) or not (8 <= len(pw) <= 63 and all(32 <= ord(c) < 127 for c in pw)
                                         or re.fullmatch(r"[0-9a-fA-F]{64}", pw or "")):
        raise ValueError("Passwort: 8 bis 63 Zeichen (nur ASCII) oder leer bei offenem Netz")


def do_scan(iface):
    check_iface(iface)
    nm("radio", "wifi", "on")
    r = nm("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "ifname", iface, "--rescan", "yes", timeout=40)
    best = {}
    for line in r.stdout.splitlines():
        p = split_terse(line)
        if len(p) < 4 or not p[1]:
            continue
        sig = int(p[2]) if p[2].isdigit() else 0
        cur = best.get(p[1])
        if cur is None or sig > cur["signal"] or p[0] == "*":
            best[p[1]] = {"ssid": p[1], "signal": sig, "security": p[3] or "offen", "in_use": p[0] == "*" or bool(cur and cur["in_use"])}
    nets = sorted(best.values(), key=lambda n: (not n["in_use"], -n["signal"]))
    write_status(scan={"iface": iface, "nets": nets[:40], "scanned": int(time.time())})
    return f"{len(nets)} Netze gefunden"


def do_connect(req):
    iface, ssid, pw = req.get("iface"), req.get("ssid"), req.get("password", "")
    check_iface(iface)
    check_ssid(ssid)
    check_password(pw)
    if ssid in saved_wifi():                        # altes Profil ersetzen (z. B. geändertes Passwort)
        nm("con", "delete", "id", ssid)
    args = ["--ask", "dev", "wifi", "connect", ssid, "ifname", iface]
    if req.get("hidden") is True:
        args += ["hidden", "yes"]
    r = nm(*args, stdin=(pw + "\n") if pw else None, timeout=60)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().replace(pw, "***") if pw else (r.stderr or r.stdout).strip()
        raise RuntimeError("Verbinden fehlgeschlagen: " + (msg[:160] or "unbekannter Fehler"))
    nm("con", "modify", "id", ssid, "connection.autoconnect", "yes", "ipv4.route-metric", "600")
    return f"Mit „{ssid}“ verbunden"


def do_forget(req):
    ssid = req.get("ssid")
    check_ssid(ssid)
    if ssid not in saved_wifi():
        raise ValueError("Dieses WLAN ist nicht gespeichert")
    nm("con", "delete", "id", ssid)
    return f"„{ssid}“ vergessen"


def do_disconnect(req):
    check_iface(req.get("iface"))
    nm("dev", "disconnect", req["iface"])
    return "Getrennt"


def main():
    try:
        with open(REQ) as f:
            req = json.load(f)
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
    write_status(state="working", message="Wird ausgeführt …", action=action)
    try:
        if action == "scan":
            msg = do_scan(req.get("iface"))
        elif action == "connect":
            msg = do_connect(req)
        elif action == "forget":
            msg = do_forget(req)
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
