#!/usr/bin/env python3
"""Root-Helfer: Protokolle der Box einsammeln, von persönlichen Angaben bereinigen und zum Herunterladen bereitstellen (läuft über pipbox-logs.path).

Die Oberfläche legt eine Auslösedatei mit dem Stichwort "collect" ab; der Helfer liest sie ohne Verweisen zu folgen, löscht sie, sammelt die Journale der
IRL4YOU-Dienste, das Zustandsprotokoll, den Zustand der Dienste und eine Kurzfassung der Einstellungen und schreibt EINE Textdatei nach
/run/pipbox-logs/bundle.txt (lesbar für den Dienst, der sie ausliefert). Vorher werden Geheimnisse und persönliche Angaben ersetzt: Passwörter, Stream-ID,
Serveradressen, WLAN-Namen, IP- und MAC-Adressen, Kamera-Schlüssel, Tailscale-Namen, E-Mail-Adressen, Zugangsschlüssel.
"""
import json
import os
import re
import stat
import subprocess
import sys
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/logs-request"
RUN = "/run/pipbox-logs"
OUT = f"{RUN}/bundle.txt"
STATUS = f"{RUN}/status.json"
MAX_TOTAL = 2_000_000          # höchste Größe der Datei in Bytes (ältestes fällt zuerst weg)
MIN_SECRET = 4                 # kürzere Werte werden nicht als Geheimnis ersetzt (sonst würde zu viel ersetzt)


def read_req(path, limit=64):
    """Anfragedatei im Ordner des Benutzers pipbox lesen, ohne Verweisen (Symlinks) zu folgen und nur bis zur Höchstgröße."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def load_json(name, limit=300_000):
    """Einstellungsdatei aus dem Ordner des Benutzers pipbox lesen (ohne Verweisen zu folgen, nur normale Dateien, begrenzte Größe). Fehler: None."""
    try:
        fd = os.open(f"{STATE}/{name}", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return json.loads(os.read(fd, limit).decode("utf-8", "replace"))
    except ValueError:
        return None
    finally:
        os.close(fd)


def write_status(**kw):
    os.makedirs(RUN, exist_ok=True)
    os.chmod(RUN, 0o755)
    kw["time"] = int(time.time())
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATUS)


# ---------------------------------------------------------------- Bereinigen

SECRET_KEYS = r"(?:pass(?:word|wd)?|psk|secret|token|stream[_-]?id|api[_-]?key|authorization|cookie|private[_-]?key)"
RE_KV = re.compile(r"(?i)\b(" + SECRET_KEYS + r")\b([\"']?\s*[=:]\s*)(?:(?:Bearer|Basic)\s+)?(\"[^\"]*\"|'[^']*'|[^\s,;&}]+)")
RE_MAC = re.compile(r"\b([0-9A-Fa-f]{2})\\?[:-]([0-9A-Fa-f]{2})\\?[:-]([0-9A-Fa-f]{2})(?:\\?[:-][0-9A-Fa-f]{2}){3}\b")      # auch mit "\:" (nmcli -t maskiert den Doppelpunkt)
RE_DEV = re.compile(r"\bdev_(?:[0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2}\b")
RE_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
RE_IPV6 = re.compile(r"(?i)(?<![0-9a-z:.])(?:(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}"
                     r"|(?:[0-9a-f]{1,4}:){1,6}(?::[0-9a-f]{1,4}){1,6}"
                     r"|(?:[0-9a-f]{1,4}:){1,7}:|::(?:[0-9a-f]{1,4}:){0,5}[0-9a-f]{1,4}"
                     r"|(?:[0-9a-f]{1,4}:){6}[0-9a-f]{1,4})(?![0-9a-z:])")
RE_DJI_KEY = re.compile(r"\bdji-[0-9a-f]{4,12}\b")
RE_EMAIL = re.compile(r"[\w.+-]{1,64}@[A-Za-z][\w-]{0,62}(?:\.[\w-]{1,63}){0,4}\.[A-Za-z]{2,24}\b")          # begrenzt: lange Zeichenfolgen ohne @ dürfen nicht quadratisch lange dauern
RE_TAILNET = re.compile(r"(?i)\b[\w-]{1,63}(?:\.[\w-]{1,63}){0,4}\.ts\.net\b")
RE_URL_TS = re.compile(r"https://(?:login|console)\.tailscale\.com/\S+")
KEEP_IPS = ("127.", "0.0.0.0", "255.")
KEEP_MACS = ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff")


class Scrubber:
    """Ersetzt persönliche Angaben und Geheimnisse. Gleiche Werte bekommen gleiche Platzhalter (so bleibt erkennbar, was zusammengehört)."""

    def __init__(self):
        self.literals = {}          # Wert -> Platzhalter
        self.maps = {}              # Art -> {Wert: Nummer}

    def secret(self, value, label):
        v = str(value or "").strip()
        if len(v) >= MIN_SECRET and v not in self.literals:
            n = sum(1 for p in self.literals.values() if p.startswith("<" + label))
            self.literals[v] = "<%s-%d>" % (label, n + 1)

    def secret_key(self, value):
        """Kamera-Schlüssel (Namen der Videoeingänge): gleiche Nummern wie die Schlüssel, die der Text selbst verrät."""
        v = str(value or "").strip()
        if len(v) >= MIN_SECRET and v not in self.literals:
            self.literals[v] = "<Schlüssel-%d>" % self._num("key", v)

    def _num(self, kind, key):
        m = self.maps.setdefault(kind, {})
        return m.setdefault(key, len(m) + 1)

    def scrub(self, text):
        for v in sorted(self.literals, key=len, reverse=True):          # lange Werte zuerst (ein WLAN-Name kann in einem anderen stecken)
            text = text.replace(v, self.literals[v])
        text = RE_KV.sub(lambda m: m.group(1) + m.group(2) + "<entfernt>", text)
        if "tailscale.com" in text:
            text = RE_URL_TS.sub("<Tailscale-Adresse>", text)
        if ".ts.net" in text:
            text = RE_TAILNET.sub("<Tailscale-Name>", text)
        if "@" in text:
            text = RE_EMAIL.sub("<E-Mail>", text)
        text = RE_DEV.sub(lambda m: "dev_<MAC-%d>" % self._num("mac", m.group(0)[4:].replace("_", ":").upper()), text)
        text = RE_MAC.sub(lambda m: m.group(0) if m.group(0).replace("\\", "").replace("-", ":").lower() in KEEP_MACS else
                          "<MAC-%d %s:%s:%s>" % (self._num("mac", m.group(0).replace("\\", "").replace("-", ":").upper()), m.group(1).upper(), m.group(2).upper(), m.group(3).upper()), text)
        text = RE_DJI_KEY.sub(lambda m: "<Schlüssel-%d>" % self._num("key", m.group(0)), text)
        text = RE_IPV4.sub(lambda m: m.group(0) if m.group(0).startswith(KEEP_IPS) else "<IP-%d>" % self._num("ip", m.group(0)), text)
        text = RE_IPV6.sub(lambda m: m.group(0) if m.group(0) == "::1" else "<IPv6-%d>" % self._num("ip6", m.group(0).lower()), text)
        return text


def collect_secrets(sc):
    """Geheimnisse und persönliche Namen aus den Einstellungen und dem Netzwerkprogramm, damit sie überall im Text ersetzt werden."""
    load = load_json

    d = load("srtla.json")
    if isinstance(d, dict):
        for s in (d.get("servers") or []):
            if isinstance(s, dict):
                sc.secret(s.get("host"), "SERVER")
                sc.secret(s.get("streamid"), "STREAM-ID")
    d = load("dji-cameras.json")
    if isinstance(d, dict):
        for cfg in (d.get("cameras") or {}).values():
            if isinstance(cfg, dict):
                sc.secret(cfg.get("ssid"), "WLAN")
                sc.secret(cfg.get("password"), "PASSWORT")
                sc.secret_key(cfg.get("rtmp_key"))
                for n in cfg.get("saved") or []:
                    if isinstance(n, dict):
                        sc.secret(n.get("ssid"), "WLAN")
                        sc.secret(n.get("password"), "PASSWORT")
        for n in (d.get("by_connection") or {}).values():
            if isinstance(n, dict):
                sc.secret(n.get("ssid"), "WLAN")
                sc.secret(n.get("password"), "PASSWORT")
    d = load("hotspot.json")                                    # Name und Passwort des eigenen Hotspots der Box
    for h in (d.values() if isinstance(d, dict) else []):
        if isinstance(h, dict):
            sc.secret(h.get("ssid"), "HOTSPOT")
            sc.secret(h.get("password"), "PASSWORT")
    d = load("cameras.json")
    for c in (d if isinstance(d, list) else []):
        if isinstance(c, dict):
            sc.secret_key(c.get("key"))
    try:
        fd = os.open(f"{STATE}/dji-token", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            sc.secret(os.read(fd, 200).decode("utf-8", "replace").strip(), "TOKEN")
        finally:
            os.close(fd)
    except OSError:
        pass
    out = run(["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"], 8)           # gespeicherte WLAN-Namen
    for line in out.splitlines():
        p = line.split(":")
        if len(p) >= 2 and p[-1] == "802-11-wireless" and not p[0].startswith("pipbox-hotspot-"):
            sc.secret(":".join(p[:-1]), "WLAN")
    out = run(["nmcli", "-t", "-f", "SSID", "dev", "wifi", "list", "--rescan", "no"], 8)      # Netze der Umgebung (stehen oft im Journal)
    for line in out.splitlines():
        sc.secret(line.strip().replace("\\:", ":"), "WLAN")


# ---------------------------------------------------------------- Sammeln

def run(cmd, timeout=15, limit=400_000):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
        out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr and not r.stdout else "")
    except FileNotFoundError:
        return "(Programm nicht vorhanden: %s)\n" % cmd[0]
    except subprocess.TimeoutExpired:
        return "(Zeitüberschreitung: %s)\n" % " ".join(cmd[:3])
    except OSError as e:
        return "(Fehler: %s)\n" % type(e).__name__
    return out[-limit:]


def read_small(path, limit=200):
    """Kleine Datei lesen, auch aus /proc (dort geht kein seek)."""
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", "replace").strip()
    except OSError:
        return "(nicht lesbar)"


def tail_file(path, lines=200, limit=300_000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return "(nicht vorhanden)\n"
    except OSError as e:
        return "(nicht lesbar: %s)\n" % type(e).__name__
    return "\n".join(data.splitlines()[-lines:]) + "\n"


def journal(unit, lines):
    return run(["journalctl", "-u", unit, "--no-pager", "-o", "short-iso", "-n", str(lines)], 20)


def collapse_repeats(text):
    """Folgen gleicher Meldungen (nur die Uhrzeit vorne unterscheidet sich) auf eine Zeile mit Zähler zusammenfassen."""
    out, prev, count, last_t = [], None, 0, ""

    def flush():
        if count > 1:
            out.append("    (… %d weitere gleiche Zeilen%s)" % (count - 1, ", die letzte um " + last_t if re.match(r"\d{4}-\d\d-\d\dT", last_t) else ""))
    for line in text.splitlines():
        t, _, rest = line.partition(" ")
        if prev is not None and rest and rest == prev:
            count += 1
            last_t = t
            continue
        flush()
        out.append(line)
        prev, count, last_t = rest, 1, t
    flush()
    return "\n".join(out) + "\n"


def drop_noise(text, keep=15):
    """Die Statusnachrichten der Kamera (alle paar Sekunden) fallen bis auf die letzten weg: Sie füllen das Journal und helfen bei Fehlern selten."""
    out, noise = [], []
    for line in text.splitlines():
        (noise if "Statusnachricht" in line else out).append(line)
    if noise:
        out.append("(Von %d Zeilen \"Statusnachricht\" der Kameras sind nur die letzten %d enthalten:)" % (len(noise), min(keep, len(noise))))
        out.extend(noise[-keep:])
    return "\n".join(out) + "\n"


def previous_boot(kernel_lines=150, all_lines=120):
    """Ende des Journals vom vorigen Start: Wenn die Box hängt und neu gestartet wird, stehen die letzten Meldungen davor (Kernel, Dienste) nur dort;
    der Abschnitt "seit dem Start" zeigt sie nicht. Die letzte Zeile verrät, wann die Box stehen blieb."""
    nl = chr(10)
    boots = [l for l in run(["journalctl", "--list-boots", "--no-pager"], 20).splitlines() if l.strip()]
    if len(boots) < 2 or not re.match(r"\s*-?\d+\s", boots[-2]):
        persist = os.path.isdir("/var/log/journal")
        return "(kein früherer Start im Journal gefunden%s)" % ("" if persist else "; das Journal wird nicht dauerhaft gespeichert (/var/log/journal fehlt)") + nl
    out = "Starts:" + nl + nl.join(boots[-4:]) + nl
    for title, args, n in (("Kernel, letzte Zeilen vor dem Neustart", ["-k"], kernel_lines), ("Alle Dienste, letzte Zeilen vor dem Neustart", [], all_lines)):
        text = run(["journalctl", "-b", "-1"] + args + ["--no-pager", "-o", "short-iso", "-n", str(n)], 20).strip()
        out += nl + "--- %s ---" % title + nl + (collapse_repeats(text).rstrip(nl) if text else "(leer)") + nl
    return out


RTMP_STAT = "http://127.0.0.1:1936/"


def rtmp_inputs(xml=None):
    """Was jede Kamera am Eingang (nginx) sendet: Auflösung, Bildrate, Profil, Stufe, Codec, Datenrate, Zuschauer (Issue #35: ein Bild, das die Box
    nicht verarbeitet, lässt sich nur mit diesen Angaben der Quelle erklären). Die Schlüssel ersetzt der Bereiniger wie überall."""
    if xml is None:
        try:
            import urllib.request
            with urllib.request.urlopen(RTMP_STAT, timeout=3) as r:
                xml = r.read(2_000_000).decode("utf-8", "replace")
        except Exception as e:                                          # nginx aus, Statistik nicht da, Zeitüberschreitung
            return "(nicht lesbar: %s)\n" % type(e).__name__
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml)
    except Exception:
        return "(Statistik nicht auswertbar)\n"
    rows = []
    for st in root.iter("stream"):
        def f(path):
            return (st.findtext(path) or "").strip() or "?"
        pub = "sendet" if st.find("publishing") is not None else "nur Zuschauer"
        try:
            mbit = "%.1f" % ((int(st.findtext("bw_video") or 0) + int(st.findtext("bw_audio") or 0)) / 1e6)
        except ValueError:
            mbit = "?"
        rows.append("%-28s %s  %sx%s  Bildrate %s  Codec %s/%s  Profil %s  Stufe %s  Video %s kbit/s  Gesamt %s Mbit/s  Zuschauer %s  seit %s ms" % (
            f("name"), pub, f("meta/video/width"), f("meta/video/height"), f("meta/video/frame_rate"), f("meta/video/codec"), f("meta/audio/codec"),
            f("meta/video/profile"), f("meta/video/level"), str(int((st.findtext("bw_video") or "0")) // 1000) if (st.findtext("bw_video") or "0").isdigit() else "?",
            mbit, f("nclients"), f("time")))
    return ("\n".join(rows) + "\n") if rows else "(keine Streams)\n"


def rtmp_clients(xml=None):
    """Verbindungen am RTMP-Eingang, auch solche, die nichts liefern: Adresse der Gegenstelle, Programm (flashver), sendet oder schaut, Dauer, abgeworfene
    Bilder. Eine Kamera, die sich verbindet und nichts schickt (zum Beispiel eine GoPro), erscheint nur hier. Adressen und Schlüssel bereinigt der Bereiniger."""
    if xml is None:
        try:
            import urllib.request
            with urllib.request.urlopen(RTMP_STAT, timeout=3) as r:
                xml = r.read(2_000_000).decode("utf-8", "replace")
        except Exception as e:
            return "(nicht lesbar: %s)\n" % type(e).__name__
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml)
    except Exception:
        return "(Statistik nicht auswertbar)\n"
    rows = []
    for st in root.iter("stream"):
        name = (st.findtext("name") or "?").strip()
        for cl in st.iter("client"):
            def f(tag):
                return (cl.findtext(tag) or "").strip() or "?"
            role = "sendet" if cl.find("publishing") is not None else "schaut"
            rows.append("%-28s %-16s %-6s Programm %-24s seit %s ms  abgeworfen %s  AV-Versatz %s ms" % (name, f("address"), role, f("flashver")[:24], f("time"),
                                                                                                     f("dropped"), f("avsync")))
    return ("\n".join(rows) + "\n") if rows else "(keine Verbindungen)\n"


def hotspot_report():
    """Hotspot der Box (zum Beispiel für eine GoPro): Zustand des Zugangspunkts, angemeldete Geräte, vergebene Adressen und die Zeilen zu An- und Abmeldung
    aus dem Journal. Ohne Passwort (der Zugangspunkt meldet es nicht; Namen und Adressen bereinigt der Bereiniger)."""
    out = []
    for line in run(["nmcli", "-t", "-f", "DEVICE,CONNECTION", "dev"], 8).splitlines():
        dev, _, con = line.partition(":")
        if not con.startswith("pipbox-hotspot-") or not re.fullmatch(r"[A-Za-z0-9._-]{1,15}", dev):
            continue
        out.append("%s:\n%s" % (dev, run(["wpa_cli", "-i", dev, "status"], 8)))
        sta = [l for l in run(["wpa_cli", "-i", dev, "all_sta"], 8).splitlines()          # je Gerät nur das Wesentliche (Zustand, Empfang, Dauer)
               if re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", l.strip()) or re.match(r"(flags|signal|connected_time|inactive_msec|rx_packets|tx_packets)=", l)]
        out.append("Angemeldete Geräte (wpa_cli all_sta):\n" + ("\n".join(sta) or "(keine)"))
        out.append("Nachbarn (ip neigh):\n" + (run(["ip", "neigh", "show", "dev", dev], 8).strip() or "(keine)"))
        out.append("Vergebene Adressen:\n" + (tail_file("/var/lib/NetworkManager/dnsmasq-%s.leases" % dev, 20).strip() or "(keine)"))
    if not out:
        return "(kein Hotspot der Box aktiv)\n"
    lines = [l for l in run(["journalctl", "-u", "wpa_supplicant", "-u", "NetworkManager", "--no-pager", "-o", "short-iso", "-n", "600"], 15).splitlines()
             if re.search(r"(?i)AP-STA|\bSTA\b|deauth|disassoc|assoc|DHCP|dnsmasq-dhcp|hotspot", l)
             and not re.search(r"Reject scan|Failed to initiate AP scan", l)]
    out.append("Anmeldungen und Abmeldungen (Journal, letzte Zeilen):\n" + ("\n".join(lines[-60:]) or "(keine)"))
    return "\n".join(out) + "\n"


NL = chr(10)
H264_PROFILES = {66: "Baseline", 77: "Main", 88: "Extended", 100: "High", 110: "High 10", 122: "High 4:2:2", 244: "High 4:4:4", 44: "CAVLC 4:4:4"}


class _Bits:
    def __init__(self, data):
        self.d, self.pos = data, 0

    def bit(self):
        i = self.pos >> 3
        if i >= len(self.d):
            raise ValueError("zu kurz")
        v = (self.d[i] >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return v

    def bits(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self.bit()
        return v

    def ue(self):
        zeros = 0
        while self.bit() == 0:
            zeros += 1
            if zeros > 32:
                raise ValueError("ungültig")
        return (1 << zeros) - 1 + self.bits(zeros)

    def se(self):
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)


def sps_info(nal):
    """Aus einer H.264-SPS (NAL-Einheit mit Kopfbyte): Profil, Stufe, Größe, Zeilensprung, Bezugsbilder, Bildreihenfolge (POC-Typ). Zahlen, nie Bilddaten."""
    raw = bytearray()
    zeros = 0
    for b in nal[1:]:                                  # Emulationsschutz (00 00 03) entfernen
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        zeros = zeros + 1 if b == 0 else 0
        raw.append(b)
    r = _Bits(bytes(raw))
    profile, _compat, level = r.bits(8), r.bits(8), r.bits(8)
    r.ue()
    chroma = 1
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma = r.ue()
        if chroma == 3:
            r.bit()
        r.ue()
        r.ue()
        r.bit()
        if r.bit():                                    # Skalierungslisten: übersprungen
            for i in range(8 if chroma != 3 else 12):
                if r.bit():
                    last, nxt = 8, 8
                    for _ in range(16 if i < 6 else 64):
                        if nxt:
                            nxt = (last + r.se() + 256) % 256
                        last = nxt or last
    r.ue()
    poc = r.ue()
    if poc == 0:
        r.ue()
    elif poc == 1:
        r.bit()
        r.se()
        r.se()
        for _ in range(r.ue()):
            r.se()
    refs = r.ue()
    r.bit()
    w, h = r.ue() + 1, r.ue() + 1
    frame_mbs_only = r.bit()
    if not frame_mbs_only:
        r.bit()
    r.bit()
    width, height = w * 16, h * 16 * (1 if frame_mbs_only else 2)
    coded, crop = (width, height), (0, 0, 0, 0)
    if r.bit():
        cl, cr, ct, cb = r.ue(), r.ue(), r.ue(), r.ue()
        unit_x = 1 if chroma == 0 else 2
        unit_y = (1 if chroma in (0, 3) else 2) * (1 if frame_mbs_only else 2)
        width -= (cl + cr) * unit_x
        height -= (ct + cb) * unit_y
        crop = (cl * unit_x, cr * unit_x, ct * unit_y, cb * unit_y)
    info = {"profile": profile, "level": level, "width": width, "height": height, "interlaced": not frame_mbs_only, "refs": refs, "poc": poc,
            "coded": coded, "crop": crop, "vui": False}
    try:                                                   # Angaben zur Darstellung (VUI): Seitenverhältnis, Bildrate, Farbbereich; fehlen sie, bleibt es bei False
        if r.bit():
            info["vui"] = True
            if r.bit():
                idc = r.bits(8)
                info["sar"] = (r.bits(16), r.bits(16)) if idc == 255 else idc
            if r.bit():
                r.bit()
            if r.bit():
                r.bits(3)
                info["full_range"] = bool(r.bit())
                if r.bit():
                    r.bits(24)
            if r.bit():
                r.ue()
                r.ue()
            if r.bit():
                ticks, scale = r.bits(32), r.bits(32)
                if ticks:
                    info["vui_fps"] = scale / (2.0 * ticks)
    except ValueError:
        pass
    return info


def _steps(ts):
    d = [b - a for a, b in zip(ts, ts[1:])]
    if not d:
        return "?"
    s = sorted(d)
    back = sum(1 for x in d if x < 0)
    return "%d/%d/%d ms (kleinster/mittlerer/größter Schritt), Rückwärtssprünge %d" % (s[0], s[len(s) // 2], s[-1], back)


def analyze_flv(data):
    """Liest die ersten Sekunden eines FLV-Stücks und beschreibt, wie die Quelle sendet: Codec, Profil, Auflösung, Bildzeiten, Schlüsselbilder, B-Bilder,
    Tonspur. Nur Kopfdaten und Zeiten, keine Bildinhalte (Issue #35: Litchi statt DJI Fly)."""
    if len(data) < 13 or data[:3] != b"FLV":
        return "(kein FLV-Datenstrom gelesen, %d Byte)" % len(data)
    flags = data[4]
    p = 13
    vts, ats, key_ts, cts_nonzero, vcount, vcodec = [], [], [], 0, 0, set()
    sps, acfg, acodec, ptypes = None, None, set(), set()
    while p + 11 <= len(data):
        t = data[p]
        size = (data[p + 1] << 16) | (data[p + 2] << 8) | data[p + 3]
        ts = (data[p + 7] << 24) | (data[p + 4] << 16) | (data[p + 5] << 8) | data[p + 6]
        body = data[p + 11:p + 11 + size]
        if len(body) < size:
            break
        if t == 9 and body:
            vcodec.add(body[0] & 15)
            is_config = (body[0] & 15) in (7, 12) and len(body) >= 2 and body[1] == 0          # Decoder-Konfiguration, kein Bild
            if not is_config:
                vcount += 1
                vts.append(ts)
                if body[0] >> 4 == 1:
                    key_ts.append(ts)
            if (body[0] & 15) == 7 and len(body) >= 5:
                ptypes.add(body[1])
                cts = int.from_bytes(body[2:5], "big", signed=False)
                if body[1] == 1 and cts not in (0, 0xFFFFFF):
                    cts_nonzero += 1
                if body[1] == 0 and len(body) > 13 and sps is None:
                    try:
                        n_sps = body[10] & 31
                        ln = int.from_bytes(body[11:13], "big")
                        if n_sps >= 1:
                            sps = sps_info(body[13:13 + ln])
                    except (ValueError, IndexError):
                        sps = {"error": True}
        elif t == 8 and body:
            ats.append(ts)
            acodec.add(body[0] >> 4)
            if (body[0] >> 4) == 10 and len(body) >= 4 and body[1] == 0 and acfg is None:
                x = (body[2] << 8) | body[3]
                acfg = ((x >> 11) & 31, (x >> 7) & 15, (x >> 3) & 15)
        p += 11 + size + 4
    AAC_RATES = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350]
    out = ["Kopf: Bild %s, Ton %s" % ("ja" if flags & 1 else "nein", "ja" if flags & 4 else "nein")]
    if vcount:
        vname = ", ".join({7: "H.264", 12: "H.265 (Kennung 12)"}.get(c, "Kennung %d" % c) for c in sorted(vcodec))
        line = "Bild: %s" % vname
        if sps and not sps.get("error"):
            line += ", Profil %s (%d), Stufe %.1f, %dx%d%s, Bezugsbilder %d, POC-Typ %d%s" % (
                H264_PROFILES.get(sps["profile"], "?"), sps["profile"], sps["level"] / 10.0, sps["width"], sps["height"],
                ", Zeilensprung" if sps["interlaced"] else "", sps["refs"], sps["poc"], " (B-Bilder möglich)" if sps["poc"] == 0 else "")
            if any(sps.get("crop", (0, 0, 0, 0))):
                line += ", kodiert %dx%d mit Beschnitt links/rechts/oben/unten %d/%d/%d/%d" % ((sps["coded"][0], sps["coded"][1]) + tuple(sps["crop"]))
            if sps.get("vui"):
                line += ", VUI: Bildrate %s, Seitenverhältnis %s, Farbbereich %s" % (
                    ("%.2f" % sps["vui_fps"]) if "vui_fps" in sps else "?", sps.get("sar", "?"),
                    {True: "voll", False: "begrenzt"}.get(sps.get("full_range"), "?"))
        elif sps:
            line += ", SPS nicht lesbar"
        elif 12 in vcodec:
            line += " (der Dekoder der Box kann das nur mit Zusatzteil)"
        out.append(line)
        dur = (vts[-1] - vts[0]) / 1000.0 if len(vts) > 1 else 0
        out.append("Bilder: %d in %.1f s (%.1f je Sekunde), %s" % (vcount, dur, (vcount - 1) / dur if dur else 0, _steps(vts)))
        out.append("Schlüsselbilder: %d%s; Bilder mit Zeitversatz (B-Bilder): %d; Pakettypen %s" % (
            len(key_ts), (" alle %d ms" % (sum(b - a for a, b in zip(key_ts, key_ts[1:])) // (len(key_ts) - 1))) if len(key_ts) > 1 else "",
            cts_nonzero, sorted(ptypes) or "?"))
        out.append("Erster Bildzeitstempel: %d ms" % vts[0])
    else:
        out.append("Bild: keine Bildpakete gelesen")
    if ats:
        name = ", ".join({10: "AAC", 2: "MP3", 1: "ADPCM", 0: "PCM"}.get(c, "Kennung %d" % c) for c in sorted(acodec))
        extra = ""
        if acfg:
            extra = ", %s Hz, %d Kanäle" % (AAC_RATES[acfg[1]] if acfg[1] < len(AAC_RATES) else "?", acfg[2])
        out.append("Ton: %s%s, %d Pakete, %s" % (name, extra, len(ats), _steps(ats)))
        out.append("Erster Tonzeitstempel: %d ms" % ats[0])
    else:
        out.append("Ton: keine Tonpakete gelesen")
    return NL.join(out)


def gst_caps_of(text):
    """Aus der Ausgabe von `gst-launch-1.0 -v`: die Eigenschaften (Caps) am Ausgang des Parsers (h264parse) und des Dekoders (mppvideodec)."""
    found = {}
    for line in text.splitlines():
        if "caps = " not in line:
            continue
        caps = line.split("caps = ", 1)[1].strip()
        if "h264parse" in line and ".GstPad:src" in line and caps.startswith("video/x-h264") and "in" not in found:
            found["in"] = caps
        if "GstMppVideoDec" in line and ".GstPad:src" in line and caps.startswith("video/x-raw") and "out" not in found:
            found["out"] = caps
    return found


def decoder_probe(seconds=4, xml=None):
    """Dekodiert von jedem laufenden H.264-Eingang kurz (seconds) probeweise mit dem Hardware-Dekoder der Box und schreibt auf, was der Dekoder liefert
    (Format, Größe, Bildrate, Speicherart): Gründe dafür, dass der Zweig einer Quelle viel Rechenzeit braucht (Issue #35). Braucht gst-launch-1.0."""
    import shutil
    import tempfile
    import xml.etree.ElementTree as ET
    NL = chr(10)
    if xml is None:
        try:
            import urllib.request
            with urllib.request.urlopen(RTMP_STAT, timeout=3) as r:
                xml = r.read(2_000_000).decode("utf-8", "replace")
        except Exception as e:
            return "(Statistik nicht lesbar: %s)" % type(e).__name__ + NL
    try:
        root = ET.fromstring(xml)
    except Exception:
        return "(Statistik nicht auswertbar)" + NL
    names = []
    for st in root.iter("stream"):
        n = (st.findtext("name") or "").strip()
        if n and st.find("publishing") is not None and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", n):
            names.append(n)
    if not names:
        return "(keine Quelle sendet)" + NL
    if not shutil.which("gst-launch-1.0"):
        return "(gst-launch-1.0 fehlt: keine Probe-Dekodierung)" + NL
    tmp = tempfile.mkdtemp(prefix="probe-", dir=RUN if os.path.isdir(RUN) else None)
    procs = []
    try:
        for i, n in enumerate(names[:4]):
            path = os.path.join(tmp, "d%d.txt" % i)
            cmd = ["timeout", str(seconds), "gst-launch-1.0", "-v", "rtmpsrc", "location=rtmp://127.0.0.1:1935/publish/%s" % n, "!", "flvdemux", "name=d",
                   "d.video", "!", "h264parse", "!", "mppvideodec", "!", "fakesink", "sync=false"]
            fh = open(path, "wb")
            procs.append((n, path, fh, subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT)))
        parts = []
        for n, path, fh, pr in procs:
            try:
                pr.wait(seconds + 5)
            except subprocess.TimeoutExpired:
                pr.kill()
            fh.close()
            try:
                with open(path, "rb") as f:
                    text = f.read(2_000_000).decode("utf-8", "replace")
            except OSError:
                text = ""
            caps = gst_caps_of(text)
            lines = ["[%s]" % n,
                     "Eingang des Dekoders: %s" % (caps.get("in", "nichts gelesen")[:600]),
                     "Ausgang des Dekoders: %s" % (caps.get("out", "kein Bild dekodiert (H.265 oder Fehler)")[:600])]
            parts.append(NL.join(lines) + NL)
        return NL.join(parts)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def stream_probe(seconds=5, xml=None):
    """Zieht von jedem laufenden Eingang kurz (seconds) den FLV-Datenstrom bei nginx und beschreibt ihn (analyze_flv). Braucht gst-launch-1.0
    (rtmpsrc), sonst steht nur der Hinweis da. Die Schlüssel ersetzt der Bereiniger."""
    import shutil
    import tempfile
    import xml.etree.ElementTree as ET
    if xml is None:
        try:
            import urllib.request
            with urllib.request.urlopen(RTMP_STAT, timeout=3) as r:
                xml = r.read(2_000_000).decode("utf-8", "replace")
        except Exception as e:
            return "(Statistik nicht lesbar: %s)" % type(e).__name__ + NL
    try:
        root = ET.fromstring(xml)
    except Exception:
        return "(Statistik nicht auswertbar)" + NL
    names = []
    for st in root.iter("stream"):
        n = (st.findtext("name") or "").strip()
        if n and st.find("publishing") is not None and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", n):
            names.append(n)
    if not names:
        return "(keine Quelle sendet)" + NL
    if not shutil.which("gst-launch-1.0"):
        return "(gst-launch-1.0 fehlt: keine Stream-Prüfung)" + NL
    tmp = tempfile.mkdtemp(prefix="probe-", dir=RUN if os.path.isdir(RUN) else None)
    procs = []
    try:
        for i, n in enumerate(names[:6]):
            path = os.path.join(tmp, "s%d.flv" % i)
            cmd = ["timeout", str(seconds), "gst-launch-1.0", "-q", "rtmpsrc", "location=rtmp://127.0.0.1:1935/publish/%s" % n, "!", "filesink", "location=" + path]
            procs.append((n, path, subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)))
        parts = []
        for n, path, pr in procs:
            try:
                pr.wait(seconds + 5)
            except subprocess.TimeoutExpired:
                pr.kill()
            try:
                with open(path, "rb") as f:
                    data = f.read(4_000_000)
            except OSError:
                data = b""
            parts.append("[%s]%s%s%s" % (n, NL, analyze_flv(data), NL))
        return NL.join(parts)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


PROC = "/proc"


def _proc_read(path):
    try:
        with open(path, "rb") as f:
            return f.read(8192).decode("utf-8", "replace")
    except OSError:
        return ""


def _cpu_snapshot():
    """Rechenzeiten aus /proc: je Kern (gesamt, davon Leerlauf) und je Thread (Name, Takte, letzter Kern). Nur Zahlen und Namen."""
    cpus, threads = {}, {}
    for line in _proc_read(PROC + "/stat").splitlines():
        if line.startswith("cpu") and line[3:4].isdigit():
            p = line.split()
            try:
                v = [int(x) for x in p[1:9]]
            except ValueError:
                continue
            cpus[p[0]] = (sum(v), v[3] + v[4])
    try:
        pids = [n for n in os.listdir(PROC) if n.isdigit()]
    except OSError:
        pids = []
    for pid in pids:
        try:
            tids = os.listdir("%s/%s/task" % (PROC, pid))
        except OSError:
            continue
        for tid in tids:
            t = _proc_read("%s/%s/task/%s/stat" % (PROC, pid, tid))
            i = t.rfind(")")
            if i < 0:
                continue
            f = t[i + 2:].split()
            try:
                threads[(pid, tid)] = (t[t.find("(") + 1:i], int(f[11]) + int(f[12]), int(f[36]))
            except (ValueError, IndexError):
                continue
    return cpus, threads


def _unit_of(pid):
    """Dienst, zu dem ein Prozess gehört (aus der Steuergruppe), sonst Kernel oder ohne Dienst."""
    for line in _proc_read("%s/%s/cgroup" % (PROC, pid)).splitlines():
        for part in reversed(line.split("/")):
            if part.endswith((".service", ".scope")):
                return part
    if not _proc_read("%s/%s/cmdline" % (PROC, pid)):
        return "Kernel"
    return "(ohne Dienst)"


def cpu_report(seconds=2, sleep=None, top=12, top_threads=10):
    """Welche Dienste und Threads die Kerne wie stark auslasten (Momentaufnahme über <seconds> Sekunden): Last je Kern, je Dienst (100 % = ein ganzer
    Kern) und die Threads mit der größten Last samt Kern, auf dem sie zuletzt liefen. Gedacht für Fälle wie "ein Kern ist voll" (Issue #35)."""
    sleep = sleep or time.sleep
    a_cpu, a_thr = _cpu_snapshot()
    sleep(seconds)
    b_cpu, b_thr = _cpu_snapshot()
    try:
        tick = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError, AttributeError):
        tick = 100
    NL = chr(10)
    cores = []
    for name in sorted(b_cpu, key=lambda x: int(x[3:])):
        if name in a_cpu:
            dt, di = b_cpu[name][0] - a_cpu[name][0], b_cpu[name][1] - a_cpu[name][1]
            cores.append("%s %3d %%" % (name, round(100.0 * (dt - di) / dt)) if dt > 0 else "%s ?" % name)
    rows = []
    for key, (name, ticks, core) in b_thr.items():
        before = a_thr.get(key)
        if before is None:
            continue
        pct = 100.0 * (ticks - before[1]) / (seconds * tick)
        if pct >= 0.5:
            rows.append((pct, key[0], name, core))
    rows.sort(reverse=True)
    units = {}
    unit_cache = {}
    for pct, pid, name, core in rows:
        u = unit_cache.setdefault(pid, _unit_of(pid))
        units[u] = units.get(u, 0.0) + pct
    out = ["Kerne (Auslastung in %%, über %d s): %s" % (seconds, "  ".join(cores) if cores else "nicht lesbar")]
    out.append("Dienste (100 % = ein ganzer Kern):")
    for u, pct in sorted(units.items(), key=lambda kv: -kv[1])[:top]:
        out.append("  %5.1f %%  %s" % (pct, u))
    if not units:
        out.append("  (keine Last gemessen)")
    out.append("Threads mit der größten Last (Kern, auf dem sie zuletzt liefen):")
    for pct, pid, name, core in rows[:top_threads]:
        out.append("  %5.1f %%  cpu%-2d  %s  [%s]" % (pct, core, name, unit_cache.get(pid, "?")))
    return NL.join(out) + NL


def system_load():
    """Druck auf CPU, Speicher und Datenträger (PSI), freier Platz und die Prozesse mit dem größten Speicherbedarf: Gründe für Ruckeln und Abstürze."""
    NL = chr(10)
    out = []
    for kind in ("cpu", "memory", "io"):
        txt = _proc_read(PROC + "/pressure/" + kind).strip()
        if txt:
            out.append("Druck %s: %s" % (kind, txt.replace(NL, " | ")))
    out.append("Freier Platz:")
    out.append(run(["df", "-h", "/", "/run"], 8).strip())
    out.append("Speicherbedarf (größte Prozesse):")
    rows = run(["ps", "-eo", "rss,pcpu,comm", "--sort=-rss"], 8).splitlines()
    out.extend(rows[:9])
    return NL.join(out) + NL


def net_counters():
    """Zähler je Netzwerkkarte seit dem Start (Pakete, Fehler, verworfen) und die Verbindungsübersicht: zeigt Verlust und Überlast auf dem Weg."""
    NL = chr(10)
    rows = ["%-10s %12s %10s %6s %6s | %12s %10s %6s %6s" % ("Karte", "rx Byte", "rx Pakete", "rx Fehl", "rx verw", "tx Byte", "tx Pakete", "tx Fehl", "tx verw")]
    for line in _proc_read(PROC + "/net/dev").splitlines()[2:]:
        if ":" not in line:
            continue
        name, data = line.split(":", 1)
        name, v = name.strip(), data.split()
        if name == "lo" or len(v) < 16:
            continue
        rows.append("%-10s %12s %10s %6s %6s | %12s %10s %6s %6s" % (name, v[0], v[1], v[2], v[3], v[8], v[9], v[10], v[11]))
    rows.append("")
    rows.append(run(["ss", "-s"], 8).strip())
    return NL.join(rows) + NL


KERNEL_BAD = re.compile(r"(?i)fail|error|fault|timeout|timed out|reset|overflow|oom|out of memory|segfault|throttl|hung task|bug:|warn|undervolt|brownout|panic|watchdog|disconnect|denied|lockup|stall")
KERNEL_SUBSYS = re.compile(r"(?i)mpp|rkvdec|rkvenc|vdpu|rga|iep|iommu|mali|drm|cma|thermal|usb|dwc|xhci|mmc|nvme|ext4|cpufreq|regulator|ethernet|r8125|stmmac|wlan|brcm|rtl|bluetooth")
KERNEL_SEVERE = re.compile(r"(?i)oom|out of memory|segfault|hung task|panic|undervolt|brownout|throttl|lockup")
KERNEL_NOISE = re.compile(r"(?i)looking up|probing|supply property|no regulator|fiq_debugger")


def kernel_hints(n=120):
    """Zeilen des Kernel-Protokolls zu Videodekoder, Speicher, Temperatur, Abstürzen und USB-Fehlern (der bisherige Abschnitt kennt nur USB, Bluetooth, WLAN)."""
    lines = [l for l in run(["dmesg"], 10).splitlines()
             if not KERNEL_NOISE.search(l) and ((KERNEL_BAD.search(l) and KERNEL_SUBSYS.search(l)) or KERNEL_SEVERE.search(l))]
    return chr(10).join(lines[-n:]) + chr(10)


def thread_load(n=22):
    """Auslastung je Thread (Momentaufnahme über eine Sekunde): zeigt, welcher Zweig oder Dienst einen Kern voll macht (Threads von GStreamer tragen den
    Namen des Elements, zum Beispiel sbf1:src oder mppvideodec)."""
    out = run(["top", "-H", "-b", "-n", "2", "-d", "1", "-w", "200"], 10)
    frames = out.split("\ntop - ")
    last = frames[-1] if frames else out
    lines = last.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("PID "):
            return "\n".join(lines[i:i + 1 + n]) + "\n"
    return out[-3000:]


def wifi_cards():
    """Zustand der WLAN-Karten für die Fehlersuche (Issue #8, Netzsuche): Karten, Fähigkeiten, Funk, Treiber, gefundene Funkstationen. Namen und MAC-Adressen
    werden danach bereinigt."""
    out = ["Karten:\n" + run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"], 8)]
    for line in run(["nmcli", "-t", "-f", "DEVICE,TYPE", "dev"], 8).splitlines():
        dev, _, typ = line.partition(":")
        if typ.strip() == "wifi" and re.fullmatch(r"[A-Za-z0-9._-]{1,15}", dev):
            info = [l for l in run(["nmcli", "-t", "-f", "GENERAL,CAPABILITIES,INTERFACE-FLAGS,WIFI-PROPERTIES", "dev", "show", dev], 8).splitlines()
                    if l.split(":", 1)[0] not in ("GENERAL.DBUS-PATH", "GENERAL.UDI", "GENERAL.CON-UUID", "GENERAL.CON-PATH", "GENERAL.PHYS-PORT-ID")]
            drv = ""
            try:
                drv = os.path.basename(os.path.realpath(f"/sys/class/net/{dev}/device/driver"))
            except OSError:
                pass
            out.append("%s (Treiber %s):\n%s" % (dev, drv or "unbekannt", "\n".join(info)))
    out.append("Funk:\n" + run(["nmcli", "-t", "radio", "all"], 8) + run(["rfkill", "list"], 8))
    out.append("NetworkManager-Einstellungen zum WLAN:\n" + "\n".join(dict.fromkeys(
        l.strip() for l in run(["NetworkManager", "--print-config"], 10).splitlines() if re.match(r"\s*wifi\.", l))))
    out.append("Gefundene Funkstationen (ohne neuen Suchlauf):\n" +
               run(["nmcli", "-f", "IN-USE,SSID,BSSID,CHAN,FREQ,SIGNAL,SECURITY,DEVICE", "dev", "wifi", "list", "--rescan", "no"], 10))
    return "\n".join(out)


def settings_summary():
    """Kurzfassung der Einstellungen ohne Zugangsdaten (keine Passwörter, Stream-ID, Server, WLAN, Schlüssel)."""
    lines = []
    d = load_json("pipeline.json")
    if isinstance(d, dict):
        lines.append("Bildaufbau: " + json.dumps({k: v for k, v in d.items() if k != "styles"}, ensure_ascii=False, sort_keys=True))
        lines.append("Aussehen der kleinen Bilder: " + json.dumps(d.get("styles", {}), ensure_ascii=False, sort_keys=True))
    else:
        lines.append("Bildaufbau: (nicht lesbar)")
    d = load_json("srtla.json")
    if isinstance(d, dict):
        lines.append("Senden: " + json.dumps(d.get("settings", {}), ensure_ascii=False, sort_keys=True) + " | Server gespeichert: %d" % len(d.get("servers") or []))
    else:
        lines.append("Senden: (nicht lesbar)")
    d = load_json("dji-cameras.json")
    if isinstance(d, dict):
        for addr, c in (d.get("cameras") or {}).items():
            if isinstance(c, dict):
                keep = {k: c[k] for k in ("name", "model", "kind", "wifi_ifname", "resolution", "fps", "bitrate", "stabilization", "autoconnect") if k in c}
                lines.append("DJI-Kamera %s: %s | gespeicherte WLANs: %d" % (addr, json.dumps(keep, ensure_ascii=False, sort_keys=True), len(c.get("saved") or [])))
    else:
        lines.append("DJI-Kameras: (nicht lesbar)")
    d = load_json("cameras.json")
    if isinstance(d, list):
        lines.append("Kameras (RTMP): " + json.dumps([{k: c.get(k) for k in ("id", "name", "role")} for c in d if isinstance(c, dict)], ensure_ascii=False, sort_keys=True))
    return "\n".join(lines) + "\n"


def usb_devices():
    """USB-Geräte aus /sys (lsusb gibt es auf der Box nicht): Anschluss, Hersteller-/Produktnummer, Name."""
    base = "/sys/bus/usb/devices"
    rows = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return "(nicht lesbar)\n"
    for n in names:
        def rd(f):
            try:
                with open(f"{base}/{n}/{f}") as fh:
                    return fh.read(80).strip()
            except OSError:
                return ""
        vid = rd("idVendor")
        if vid and not n.startswith("usb"):
            rows.append("%-8s %s:%s %s | %s" % (n, vid, rd("idProduct"), rd("manufacturer"), rd("product")))
    return "\n".join(rows) + "\n"


def usb_events():
    """USB-Trennungen, abgeschaltete Anschlüsse und Überstrom der letzten 3 Tage (Kernel-Journal) und die Ereignisse, die die Oberfläche gemerkt hat."""
    pat = re.compile(r"usb \d+-[\d.]+: USB disconnect|disabled by hub|over-current|unable to enumerate USB device|device descriptor read/\w+, error -71|xhci_hcd.*(died|dead|host controller)")
    lines = [l for l in run(["journalctl", "-k", "--since", "-3 days", "--no-pager", "-o", "short-iso"], 20).splitlines() if pat.search(l)]
    saved = load_json("usb-events.json")
    mem = ""
    if isinstance(saved, list):
        mem = "".join("%s UTC  Anschluss %s  Art %s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(e["t"])), e.get("port", "?"), e.get("cat", "?"), e.get("why", "?"))
                      for e in saved[-20:] if isinstance(e, dict) and isinstance(e.get("t"), (int, float)))
    return ("Kernel (letzte 3 Tage):\n" + (chr(10).join(lines[-60:]) if lines else "keine Ereignisse") + "\n\nVon der Oberfläche gemerkt (Anschluss, Art des Geräts, Grund: gone = getrennt, "
            "emi = Anschluss abgeschaltet durch Störung oder Spannungseinbruch, over = Überstrom):\n" + (mem or "keine") + "\n")


def sections():
    now = time.time()
    local = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    version = read_small("/opt/pipbox/VERSION")
    head = ("IRL4YOU BOX Protokolle\n"
            "Erstellt: %s UTC (%s Ortszeit der Box). Alle Zeiten in den Protokollen sind UTC; in Deutschland ist es im Sommer UTC+2.\n"
            "Version: %s\n"
            "Bereinigt: Passwörter, Stream-ID, Serveradressen, WLAN-Namen, IP- und MAC-Adressen, Kamera-Schlüssel, Tailscale-Namen, E-Mail-Adressen und "
            "Zugangsschlüssel sind durch Platzhalter ersetzt (gleiche Werte haben gleiche Nummern). Bitte trotzdem vor dem Weitergeben kurz durchsehen.\n"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)), local, version))
    units = ["pipbox", "pipbox-dji", "pipbox-send", "pipbox-health", "bluetooth", "NetworkManager", "tailscaled", "nginx"]
    helpers = ["pipbox-swupdate", "pipbox-update", "pipbox-wifi", "pipbox-btdriver", "pipbox-remote", "pipbox-power", "pipbox-logmode"]
    out = [("Kopf", head),
           ("System", "Kernel: %s\nBetriebszeit: %s\nLast: %s\n%s" % (run(["uname", "-r"], 5).strip(), run(["uptime", "-p"], 5).strip(),
                                                                    read_small("/proc/loadavg"), run(["free", "-m"], 5))),
           ("Dienste", "".join("%-22s %s\n" % (u, run(["systemctl", "is-active", u + ".service"], 5).strip()) for u in units)
            + "Einmalige Helfer (\"inactive\" ist normal, wenn sie nicht gerade arbeiten):\n"
            + "".join("%-22s %s\n" % (u, run(["systemctl", "is-active", u + ".service"], 5).strip()) for u in helpers)
            + "Fehlgeschlagene Dienste:\n" + (run(["systemctl", "--failed", "--no-legend", "--no-pager"], 10).strip() or "keine") + "\n"),
           ("Einstellungen (Kurzfassung)", settings_summary()),
           ("Netzwerkkarten", run(["ip", "-br", "addr"], 8) + run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"], 8)),
           ("USB-Geräte", usb_devices()),
           ("WLAN-Karten (Zustand und gefundene Funkstationen)", wifi_cards()),
           ("Bluetooth", run(["hciconfig"], 8)),
           ("Zustandsprotokoll (alle 10 s, letzte Stunde)", tail_file("/var/log/pipbox-health.log", 360) if os.path.exists("/var/log/pipbox-health.log")
            else tail_file("/run/pipbox-health.log", 360)),
           ("Zustand der Sendekette (nur während des Sendens vorhanden)", tail_file("/run/pipbox-send/status.json", 80)),
           ("Sendewege (srtla_send)", tail_file("/run/pipbox-send/srtla-links.txt", 40)),
           ("Regler (belacoder, letzte Zeilen)", tail_file("/run/pipbox-send/belacoder-stats.txt", 40)),
           ("Kameras am Eingang (nginx-Statistik)", rtmp_inputs()),
           ("Verbindungen am RTMP-Eingang (auch ohne Bild, zum Beispiel GoPro)", rtmp_clients()),
           ("Hotspot der Box (angemeldete Geräte, Adressen, An- und Abmeldungen)", hotspot_report()),
           ("Bildaufbau der Sendekette (belacoder: Bilder je Sekunde und Rückstand je Zweig, Warnungen)", tail_file("/run/pipbox-send/belacoder-live.txt", 60)),
           ("Stream-Prüfung der Quellen (erste 5 Sekunden jedes Eingangs: Codec, Profil, Bildzeiten, B-Bilder, Ton)", stream_probe()),
           ("Dekoder-Ausgang je Quelle (Probe-Dekodierung, erste 4 Sekunden: Format, Größe, Bildrate, Speicherart)", decoder_probe()),
           ("Auslastung je Thread (Momentaufnahme)", thread_load()),
           ("Auslastung je Kern und je Dienst (Momentaufnahme über 2 s)", cpu_report()),
           ("System: Druck, freier Platz, Speicherbedarf", system_load()),
           ("Netzwerk-Zähler (seit dem Start)", net_counters()),
           ("Ereignisse der Sendekette (belacoder: Zweige starten, Zeitausrichtung)", tail_file("/run/pipbox-send/belacoder-events.txt", 80)),
           ("Hänger der Sendekette (Diagnosepakete des Wächters: Threads von belacoder, Zustand D, Statistik; Neustart erfolgte automatisch)", tail_file("/var/lib/pipbox/hang-diagnose.txt", 400)),
           ("nginx: letzte Fehler", tail_file("/var/log/nginx/error.log", 40)),
           ("Zustand der Software-Updates", tail_file("/run/pipbox-swupdate/status.json", 40)),
           ("Protokoll der Software-Updates", collapse_repeats(tail_file("/var/log/pipbox-swupdate.log", 150))),
           ("Protokoll der System-Updates", collapse_repeats(tail_file(f"{STATE}/update.log", 80)))]
    for u, n in (("pipbox-dji", 700), ("pipbox-send", 700), ("pipbox", 400), ("pipbox-wifi", 120), ("pipbox-btdriver", 120), ("pipbox-swupdate", 200),
                 ("pipbox-update", 100), ("pipbox-remote", 100), ("pipbox-power", 40)):
        text = journal(u, n)
        out.append(("Journal %s" % u, collapse_repeats(drop_noise(text) if u == "pipbox-dji" else text)))
    # Tailscale: Zustand der Freigaben (privat und öffentlich) und die Zeilen des Dienstes, die Freigabe, Zertifikat, Anmeldung und Fehler betreffen
    # (der Rest, zum Beispiel Verbindungsaufbau zu den Gegenstellen, ist Rauschen)
    ts = ("serve:\n" + (run(["tailscale", "serve", "status"], 10).strip() or "(leer)") + "\n\nfunnel:\n"
          + (run(["tailscale", "funnel", "status"], 10).strip() or "(leer)") + "\n")
    out.append(("Tailscale (Freigabe)", ts))
    tsj = [l for l in journal("tailscaled", 3000).splitlines()
           if re.search(r"(?i)serve|funnel|cert|acme|login|auth|expire|error|fail|warn|denied|health|hostinfo|netmap.*(changed|self)", l)
           and not re.search(r"(?i)portmapper|magicsock|derp|disco|netcheck", l)]
    out.append(("Journal tailscaled (nur Freigabe, Zertifikat, Anmeldung, Fehler)", "\n".join(tsj[-120:]) + "\n"))
    out.append(("Warnungen und Fehler des Systems (seit dem Start)", run(["journalctl", "-p", "warning", "-b", "--no-pager", "-o", "short-iso", "-n", "200"], 20)))
    out.append(("Vorheriger Start (Ende des Journals: Kernel und Dienste vor dem letzten Neustart, zum Beispiel nach einem Hänger)", previous_boot()))
    out.append(("USB-Ereignisse (Trennungen, abgeschaltete Anschlüsse; Ausfall eines Adapters oder Routers)", usb_events()))
    out.append(("Kernel (Video, Speicher, Temperatur, Abstürze, USB-Fehler)", kernel_hints()))
    out.append(("Kernel (USB, Bluetooth, WLAN)", "\n".join([l for l in run(["dmesg"], 10).splitlines()
                                                              if re.search(r"(?i)usb|bluetooth|btusb|wlan|wifi|cfg80211|rtl|brcm", l)][-150:]) + "\n"))
    return out


def build():
    sc = Scrubber()
    collect_secrets(sc)
    parts = []
    for title, body in sections():
        parts.append("\n===== %s =====\n%s" % (title, body.rstrip("\n") + "\n"))
    text = sc.scrub("".join(parts))
    if len(text.encode("utf-8")) > MAX_TOTAL:                          # zu groß: vom Ende kürzen (die jüngsten Abschnitte bleiben, der Kopf immer)
        head, rest = text.split("\n===== System =====", 1)
        rest = rest.encode("utf-8")[-(MAX_TOTAL - len(head.encode("utf-8")) - 200):].decode("utf-8", "ignore")
        text = head + "\n(Datei zu groß: der Anfang wurde gekürzt)\n===== System (gekürzt) =====" + rest.split("\n", 1)[-1]
    return text


def main():
    try:
        req = read_req(REQ).strip()
    except (OSError, ValueError):
        req = ""
    try:
        os.remove(REQ)
    except OSError:
        pass
    if req != "collect":
        write_status(state="error", message="Ungültige Anfrage")
        return 1
    write_status(state="working", message="Protokolle werden gesammelt …")
    try:
        text = build()
        os.makedirs(RUN, exist_ok=True)
        os.chmod(RUN, 0o755)
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, OUT)
        write_status(state="done", message="Fertig", size=len(text.encode("utf-8")), version=read_small("/opt/pipbox/VERSION"))
    except Exception as e:                                                                   # nie ohne Meldung enden
        write_status(state="error", message="Fehler beim Sammeln: " + type(e).__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
