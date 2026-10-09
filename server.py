#!/usr/bin/env python3
"""IRL4YOU BOX: eigene Oberfläche, Sendesteuerung und Auslastungsanzeige, getrennt von belaUI.

Nur Python-Standardbibliothek. Läuft auf der Box (liest /proc und /sys) und
mit --demo auch auf dem Mac (erzeugte Beispielwerte) zur Ansicht.

Der Dienst lauscht auf dem Port 8780 (install/pipbox.service: --host 0.0.0.0, erreichbar im lokalen Netz);
Fernzugriff von unterwegs läuft über `tailscale serve`.
"""
import argparse
import base64
import binascii
import glob
import hashlib
import ipaddress
import hmac
import json
import math
import os
import collections
import random
import re
import secrets
import shutil
import socket
import sys
import ssl
import stat
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import controller_keys            # Tasten gekoppelter Bluetooth-Controller und ihre Funktionen
import dji                         # Namen von USB-Sticks (WLAN, Bluetooth) aus /sys, liegt neben dieser Datei
import hdmi_daemon                 # Prüfung der HDMI-Einstellungen (dieselbe wie im HDMI-Dienst), liegt neben dieser Datei
import pipbox_live                 # Engine "alle Kameras immer bereit" mit Compositor und dynamischen Zweigen (Technik von streamingbox), liegt neben dieser Datei
try:
    import pipbox_preview          # Vorschau des Sendebilds (Issue #52), liegt neben dieser Datei; fehlt sie (halb eingespieltes Update), gibt es nur keine Vorschau
except ImportError:
    pipbox_preview = None

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# Echte Netzwerkkarten für die Upload-Anzeige (Ethernet, WLAN, USB-/Mobilfunk-Modems). Virtuelles (Tailscale, Docker,
# Brücken) bleibt draußen, sonst würde der Verkehr doppelt gezählt. Angezeigt wird, was gerade verbunden ist.
NET_PREFIXES = ("eth", "en", "wlan", "wl", "usb", "wwan", "ppp", "rndis")
NET_SKIP = ("docker", "veth", "br-", "virbr", "tailscale", "tun", "tap", "lo")

# Ampelgrenzen (Rohwerte, später anhand echter Messungen festlegen)
LIMITS = {"temp_warn": 70.0, "temp_crit": 80.0, "cpu_warn": 85.0, "core_warn": 90.0, "mem_warn": 85.0}


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


BC_STATS_FILE = "/run/pipbox-send/belacoder-stats.json"        # vom Sender (belacoder-stats.patch), einmal je Sekunde
THERMAL_NAMES = {"soc-thermal": "SoC", "bigcore0-thermal": "Große Kerne 4–5", "bigcore1-thermal": "Große Kerne 6–7",
                 "littlecore-thermal": "Kleine Kerne 0–3", "center-thermal": "Mitte", "gpu-thermal": "GPU", "npu-thermal": "NPU"}


def devfreq_loads():
    """Auslastung von GPU und NPU (und Speichercontroller) aus /sys/class/devfreq/*/load ("12@300000000Hz"): {"gpu": {...}, "npu": {...}}."""
    out = {}
    try:
        names = sorted(os.listdir("/sys/class/devfreq"))
    except OSError:
        return out
    for n in names:
        kind = "gpu" if "gpu" in n else "npu" if "npu" in n else None
        if not kind:
            continue
        raw = (read(f"/sys/class/devfreq/{n}/load", "") or "").strip()
        m = re.match(r"^(\d+)@(\d+)Hz$", raw)
        if m:
            # Der Frequenzregler der NPU (rknpu_ondemand) meldet immer 100 %, auch im Leerlauf (gemessen, die echte Last steht nur in debugfs,
            # und das liest nur root): bei der NPU deshalb nur die Frequenz, die Auslastung bleibt unbekannt.
            out[kind] = {"load_pct": int(m.group(1)) if kind == "gpu" else None, "mhz": int(m.group(2)) // 1000000}
    return out


def disk_usage(path="/"):
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
    return {"total_gb": round(total / 1e9, 1), "free_gb": round(free / 1e9, 1), "used_gb": round((total - st.f_bfree * st.f_frsize) / 1e9, 1),
            "used_pct": round(100.0 * (1 - free / total), 1) if total else None}


def send_stats():
    """Kennzahlen der Sendung aus der Datei von belacoder (Bitrate, Laufzeit, Sendepuffer, Neuübertragungen, Verlust, Encoder-Bilder je Sekunde).
    None, wenn keine frische Datei da ist (Sendung aus oder ältere belacoder-Fassung)."""
    try:
        if time.time() - os.stat(BC_STATS_FILE).st_mtime > 6:
            return None
        d = json.loads(read(BC_STATS_FILE, "") or "{}")
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not d:
        return None
    sent = d.get("sent_total")
    out = {k: d.get(k) for k in ("fps", "bitrate_kbps", "rtt_ms", "send_mbps", "snd_buf_pkts", "snd_buf_ms", "retrans_total", "loss_total", "drop_total", "sent_total")}
    for k in ("retrans", "loss"):
        t = d.get(k + "_total")
        out[k + "_pct"] = round(100.0 * t / sent, 2) if isinstance(t, (int, float)) and isinstance(sent, (int, float)) and sent > 0 and t >= 0 else None
    return out


class UsbWatch:
    """Merkt sich, wenn ein USB-Gerät ausfällt oder abgezogen wird (zum Beispiel der USB-WLAN-Adapter als Sendeweg, ein Router, ein Bluetooth-Stick).
    Quelle ist das Kernel-Protokoll (dmesg, ohne Rechte lesbar): "usb 2-1: USB disconnect", "usb usb2-port1: disabled by hub (EMI?)" und "over-current".
    Die Ereignisse stehen mit Zeit in <state>/usb-events.json (überstehen einen Neustart des Dienstes; ein Ausfall, bei dem die ganze Box stehen bleibt, ist
    für den Kernel nicht zu sehen). Die Oberfläche zeigt sie als Meldung: Art des Geräts (aus dem Produktnamen) und Uhrzeit. Kommt das Gerät wieder (der Kernel
    erkennt am selben Anschluss ein neues Gerät), steht „wieder da“ dabei und die Meldung verschwindet nach BACK_SHOW (10 Minuten); kommt es nicht wieder, bleibt
    sie bis WINDOW (24 Stunden) oder bis der Nutzer sie mit dem × schließt (dismiss)."""
    WINDOW = 24 * 3600.0
    BACK_SHOW = 600.0
    EVERY = 20.0
    KEEP = 20
    RE_LINE = re.compile(r"^(\d{4}-\d\d-\d\dT[\d:.,]+[+-]\d\d:\d\d) (.*)$")
    RE_PROD = re.compile(r"^usb (\d+-[\d.]+): Product: (.{1,80})$")
    RE_NEW = re.compile(r"^usb (\d+-[\d.]+): new .*USB device number \d+")
    RE_GONE = re.compile(r"^usb (\d+-[\d.]+): USB disconnect, device number \d+$")
    RE_HUB = re.compile(r"^usb usb(\d+)-port(\d+): (disabled by hub|over-current condition)")
    CATS = (("wlan", re.compile(r"(?i)802\.11|wlan|wireless|wi-?fi|\bnic\b|rtl88|rtl81|ralink|mediatek.*wlan")),
            ("bt", re.compile(r"(?i)bluetooth|\bbt\d|usb-bt")),
            ("net", re.compile(r"(?i)rndis|ethernet|\bcdc\b|router|modem|lte|\b4g\b|\b5g\b|mudi|gl-?inet|tether|android|hotspot|gadget")),
            ("cam", re.compile(r"(?i)camera|uvc|capture|hdmi|video|osmo|action|dji|webcam")))

    def __init__(self, path, runner=None, clock=time.time, demo=False):
        self.path, self.runner, self.clock, self.demo = path, runner or self._dmesg, clock, demo
        self.lock = threading.Lock()
        self.events, self.last = [], 0.0
        self.demo_t, self.demo_closed = clock() - 2100, False              # Vorschau: eine feste Beispielmeldung, die sich schließen lässt
        try:
            with open(path) as f:
                saved = json.load(f)
            if isinstance(saved, list):
                self.events = [e for e in saved if isinstance(e, dict) and isinstance(e.get("t"), (int, float)) and isinstance(e.get("cat"), str)][-self.KEEP:]
        except (OSError, ValueError):
            pass

    @staticmethod
    def _dmesg():
        try:
            return subprocess.run(["dmesg", "--time-format", "iso"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    @classmethod
    def category(cls, name):
        for cat, rx in cls.CATS:
            if rx.search(name or ""):
                return cat
        return "other"

    def _parse(self, text):
        import datetime
        prod, out, backs = {}, [], []
        for raw in text.splitlines()[-6000:]:
            m = self.RE_LINE.match(raw)
            if not m:
                continue
            try:
                t = datetime.datetime.fromisoformat(m.group(1).replace(",", ".")).timestamp()
            except ValueError:
                continue
            msg = m.group(2)
            a = self.RE_PROD.match(msg)
            if a:
                prod[a.group(1)] = a.group(2).strip()
                continue
            n = self.RE_NEW.match(msg)
            if n:
                backs.append((t, n.group(1)))
                continue
            g = self.RE_GONE.match(msg)
            if g:
                out.append({"t": round(t, 1), "port": g.group(1), "cat": self.category(prod.get(g.group(1), "")), "why": "gone"})
                continue
            h = self.RE_HUB.match(msg)
            if h:
                port = "%s-%s" % (h.group(1), h.group(2))
                out.append({"t": round(t, 1), "port": port, "cat": self.category(prod.get(port, "")), "why": "emi" if h.group(3).startswith("disabled") else "over"})
        merged = []
        for e in sorted(out, key=lambda x: x["t"]):                       # Trennung und abgeschalteter Anschluss kurz hintereinander am selben Anschluss: ein Ereignis
            if merged and merged[-1]["port"] == e["port"] and e["t"] - merged[-1]["t"] < 5:
                if e["why"] != "gone":
                    merged[-1]["why"] = e["why"]
                continue
            merged.append(e)
        for e in merged:                                                    # am selben Anschluss wurde danach ein Gerät neu erkannt: wieder da
            for bt, bport in backs:
                if bport == e["port"] and 0 < bt - e["t"] < 3600:
                    e["back"] = round(bt, 1)
                    break
        return merged

    def scan(self):
        """Kernel-Protokoll lesen und neue Ereignisse aufnehmen (höchstens alle EVERY Sekunden)."""
        with self.lock:
            now = self.clock()
            if now - self.last < self.EVERY:
                return
            self.last = now
        try:
            found = self._parse(self.runner())
        except Exception:
            return
        with self.lock:
            have = {(round(e["t"]), e.get("port")): e for e in self.events}
            new = [e for e in found if (round(e["t"]), e["port"]) not in have]
            changed = False
            for f in found:                                                 # ein schon gemerktes Ereignis bekommt nachträglich "wieder da"
                old = have.get((round(f["t"]), f["port"]))
                if old is not None and f.get("back") and not old.get("back"):
                    old["back"] = f["back"]
                    changed = True
            if not new and not changed:
                return
            self.events = (self.events + new)[-self.KEEP:]
            data = list(self.events)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def alerts(self):
        """Meldungen für die Oberfläche: die jüngsten Ereignisse der letzten 24 Stunden (höchstens zwei), je ein Eintrag mit Art und Zeit. Weg sind Meldungen,
        die der Nutzer geschlossen hat, und solche, deren Gerät seit mehr als BACK_SHOW Sekunden wieder da ist."""
        if self.demo:
            return [] if self.demo_closed else [{"level": "warn", "kind": "usb", "cat": "wlan", "why": "emi", "t": int(self.demo_t), "back": False}]
        self.scan()
        now = self.clock()
        with self.lock:
            fresh = [e for e in self.events if now - e["t"] <= self.WINDOW and not e.get("ack") and not (e.get("back") and now - e["back"] > self.BACK_SHOW)]
        return [{"level": "warn", "kind": "usb", "cat": e["cat"], "why": e.get("why", "gone"), "t": int(e["t"]), "back": bool(e.get("back"))}
                for e in sorted(fresh, key=lambda x: -x["t"])[:2]]

    def dismiss(self, t):
        """Die Meldung mit dieser Zeit (ganze Sekunden, wie in alerts()) schließen. Gibt zurück, ob eine gefunden wurde."""
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            raise ValueError("Ungültige Anfrage")
        if self.demo:
            self.demo_closed = True
            return True
        found = False
        with self.lock:
            for e in self.events:
                if int(e["t"]) == int(t) and not e.get("ack"):
                    e["ack"] = True
                    found = True
            data = list(self.events)
        if found and not self.demo:
            try:
                tmp = self.path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(data, f)
                os.replace(tmp, self.path)
            except OSError:
                pass
        return found


class OutageWatch:
    """Merkt sich, wenn eine Kamera ausfällt (sie hat gesendet und sendet nicht mehr) oder der HDMI-Eingang kein Signal hat, und liefert kurze Meldungen für die
    Oberfläche: {"kind": "cam"|"hdmi", ...}. Eine Kamera gilt als ausgefallen, wenn sie seit dem Start des Dienstes einmal gesendet hat und jetzt länger als DOWN
    Sekunden nicht mehr; die Meldung verschwindet, sobald sie wieder sendet (oder nach MAX Stunden). Ohne Zustand auf der Platte: nach einem Neustart des Dienstes
    beginnt die Beobachtung neu."""
    DOWN = 20.0
    MAX = 6 * 3600.0

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.seen = {}                 # Kamera-Schlüssel -> hat gesendet
        self.since = {}                # Kamera-Schlüssel -> seit wann nicht mehr (Zeit) oder fehlt
        self.hdmi_since = None
        self.closed = set()            # vom Nutzer mit dem × geschlossene Meldungen: (Art, Zeit des Ausfalls); kommt die Kamera wieder, gilt ein neuer Ausfall wieder

    def dismiss(self, kind, t):
        """Eine Meldung "Kamera ausgefallen" oder "HDMI: kein Signal" schließen (zum Beispiel nach einem bewussten Abziehen). Gibt zurück, ob die Art bekannt ist."""
        if kind not in ("cam", "hdmi") or isinstance(t, bool) or not isinstance(t, (int, float)):
            raise ValueError("Ungültige Anfrage")
        with self.lock:
            self.closed.add((kind, int(t)))
        return True

    def alerts(self, cameras, hdmi=None):
        now = self.clock()
        out = []
        with self.lock:
            self.closed = {c for c in self.closed if now - c[1] <= self.MAX}                 # alte Einträge fallen weg
            keys = set()
            for c in cameras or []:
                key = c.get("key")
                if not isinstance(key, str):
                    continue
                keys.add(key)
                state = c.get("state")
                if state == "live":
                    self.seen[key] = True
                    self.since.pop(key, None)
                elif state == "offline" and self.seen.get(key):
                    self.since.setdefault(key, now)
                    t = self.since[key]
                    if key != HDMI_KEY and now - t >= self.DOWN and now - t <= self.MAX and ("cam", int(t)) not in self.closed:
                        kind = "dji" if str(key).startswith("dji-") else "cam"
                        out.append({"level": "warn", "kind": "cam", "cam": kind, "name": str(c.get("name") or "")[:40], "t": int(t)})
            for k in list(self.seen):
                if k not in keys:
                    self.seen.pop(k, None)
                    self.since.pop(k, None)
            # HDMI-Eingang: Dienst meldet einen Fehler oder (eingeschaltet) kein Signal
            bad = False
            if isinstance(hdmi, dict) and hdmi.get("service") is not False and hdmi.get("available") is not False:
                en = bool((hdmi.get("settings") or {}).get("enabled"))
                sig = hdmi.get("signal") or {}
                bad = hdmi.get("state") == "error" or (en and hdmi.get("signal_known") and sig.get("plugged") is False)
            if bad:
                if self.hdmi_since is None:
                    self.hdmi_since = now
                if self.DOWN <= now - self.hdmi_since <= self.MAX and ("hdmi", int(self.hdmi_since)) not in self.closed:
                    out.append({"level": "warn", "kind": "hdmi", "src": (hdmi.get("settings") or {}).get("source", "hdmi"), "t": int(self.hdmi_since)})
            else:
                self.hdmi_since = None
        return out


class Sampler:
    """Berechnet Raten aus Zählerdifferenzen zwischen zwei Abfragen."""

    def __init__(self, demo):
        self.demo = demo
        self.prev_cpu = None
        self.prev_net = None
        self.prev_t = None
        self._lock = threading.Lock()

    def cpu_times(self):
        out = []
        for line in (read("/proc/stat", "") or "").splitlines():
            if line.startswith("cpu") and line[3:4].isdigit():
                v = list(map(int, line.split()[1:]))
                idle = v[3] + (v[4] if len(v) > 4 else 0)
                out.append((sum(v), idle))
        return out

    @staticmethod
    def fan_pwm():
        """Ansteuerung des Lüfters (PWM 0..255) oder None. Das ist der Sollwert, keine gemessene Drehzahl:
        die Box hat keinen Drehzahlanschluss am Lüfter."""
        try:
            for d in sorted(os.listdir("/sys/class/hwmon")):
                base = f"/sys/class/hwmon/{d}"
                if read(base + "/name", "").strip() == "pwmfan":
                    v = read(base + "/pwm1")
                    return int(v) if v is not None and 0 <= int(v) <= 255 else None
        except (OSError, ValueError):
            pass
        return None

    @staticmethod
    def blocked_threads(proc="/proc", limit=6):
        """Threads im Kernel-Zustand D (nicht unterbrechbar) mit Wartestelle im Kernel: ["usb-storage@usb_sg_wait", ...]. Zeigt, WER hinter der Meldung
        "n Prozess(e) blockiert" steckt (oft ein USB-Modem, das sich zusätzlich als CD-Laufwerk meldet, oder der Treiber eines Geräts)."""
        out = []
        for d in sorted(glob.glob(proc + "/[0-9]*/task/[0-9]*")):
            t = read(d + "/stat", "") or ""
            i = t.rfind(")")
            if i < 0 or t[i + 2:i + 3] != "D":
                continue
            name = t[t.find("(") + 1:i].replace(" ", "_")
            wchan = (read(d + "/wchan", "") or "").strip()
            out.append("%s@%s" % (name, wchan if wchan and wchan != "0" else "?"))
            if len(out) >= limit:
                break
        return out

    def net_bytes(self):
        res = {}
        for line in (read("/proc/net/dev", "") or "").splitlines()[2:]:
            name, _, rest = line.partition(":")
            name = name.strip()
            if name.startswith(NET_PREFIXES) and not name.startswith(NET_SKIP) and \
                    (read(f"/sys/class/net/{name}/operstate", "") or "").strip() in ("up", "unknown"):
                f = rest.split()
                res[name] = (int(f[0]), int(f[8]))
        if pipbox_preview:
            res = pipbox_preview.TRAFFIC.adjust(res)            # der Verkehr der Vorschau zum Browser zählt nicht zum Upload der Sendung
        return res

    def sample(self):
        """Messwerte. Liegt die letzte Messung keine 1,5 s zurück (zweiter Tab, mehrere Abfragen), gilt sie weiter:
        sonst würden die Raten über sehr kurze Zeiträume berechnet und die Arbeit doppelt gemacht."""
        if self.demo:
            return self.sample_demo()
        with self._lock:
            hit = getattr(self, "_last", None)
            if hit is not None and time.monotonic() - hit[0] < 1.5:
                return hit[1]
            val = self.sample_real()
            self._last = (time.monotonic(), val)
            return val

    def sample_real(self):
        now = time.time()
        cpu, net = self.cpu_times(), self.net_bytes()
        cores, rates = [], {}
        if self.prev_cpu and len(self.prev_cpu) == len(cpu):
            for (t1, i1), (t0, i0) in zip(cpu, self.prev_cpu):
                dt = max(t1 - t0, 1)
                cores.append(round(100.0 * (1 - (i1 - i0) / dt), 1))
        dt_s = (now - self.prev_t) if self.prev_t else None
        if self.prev_net and dt_s:
            for n, (rx, tx) in net.items():
                p = self.prev_net.get(n)
                if p:
                    rates[n] = {"rx_mbit": max(0.0, round((rx - p[0]) * 8 / dt_s / 1e6, 2)),
                                "tx_mbit": max(0.0, round((tx - p[1]) * 8 / dt_s / 1e6, 2))}
        self.prev_cpu, self.prev_net, self.prev_t = cpu, net, now

        freqs = []
        i = 0
        while True:
            v = read(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq")
            if v is None:
                break
            freqs.append(int(v) // 1000)
            i += 1
        temps = []
        z = 0
        while True:
            v = read(f"/sys/class/thermal/thermal_zone{z}/temp")
            if v is None:
                break
            temps.append(int(v) / 1000.0)
            z += 1
        mem = {}
        for line in (read("/proc/meminfo", "") or "").splitlines():
            k, _, v = line.partition(":")
            mem[k] = int(v.split()[0]) if v.split() else 0
        total, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
        blocked = 0
        for line in (read("/proc/stat", "") or "").splitlines():
            if line.startswith("procs_blocked"):
                blocked = int(line.split()[1])
        zones = []
        for zi in range(len(temps)):
            zt = (read(f"/sys/class/thermal/thermal_zone{zi}/type", "") or "").strip()
            zones.append({"name": THERMAL_NAMES.get(zt, zt or f"Zone {zi}"), "c": round(temps[zi], 1)})
        out = self.finish(cores, freqs, max(temps) if temps else None,
                          total, avail, rates, blocked, self.fan_pwm(), self.blocked_threads() if blocked > 0 else None)
        out["details"] = {"temps": zones, "accel": devfreq_loads(), "disk": disk_usage("/"), "send": send_stats()}
        return out

    def sample_demo(self):
        t = time.time()
        cores = [round(max(2, min(99, 45 + 35 * math.sin(t / 7 + k) +
                                  random.uniform(-6, 6))), 1) for k in range(8)]
        freqs = [1800 if k >= 4 else 1416 for k in range(8)]
        temp = 52 + 8 * math.sin(t / 30) + random.uniform(-0.5, 0.5)
        rates = {"eth0": {"rx_mbit": 0.4, "tx_mbit": round(6 + random.uniform(-1, 1), 2)},
                 "eth1": {"rx_mbit": round(13 + random.uniform(-1, 1), 2),
                          "tx_mbit": round(5 + random.uniform(-1, 1), 2)}}
        out = self.finish(cores, freqs, temp, 16 * 1024 * 1024,
                          int(7.4 * 1024 * 1024), rates, 0, int(110 + 40 * math.sin(t / 30)))
        out["details"] = {
            "temps": [{"name": n, "c": round(temp + d + random.uniform(-0.4, 0.4), 1)} for n, d in
                      (("SoC", 0), ("Große Kerne 4–5", 2), ("Große Kerne 6–7", 1.5), ("Kleine Kerne 0–3", -3), ("Mitte", 0), ("GPU", -5), ("NPU", -6))],
            "accel": {"gpu": {"load_pct": int(12 + 8 * math.sin(t / 5)), "mhz": 600}, "npu": {"load_pct": None, "mhz": 1000}},
            "disk": {"total_gb": 62.8, "free_gb": 55.5, "used_gb": 4.6, "used_pct": 8.7},
            "send": {"fps": 29.9, "bitrate_kbps": 9800, "rtt_ms": round(38 + 6 * math.sin(t / 9), 1), "send_mbps": round(9.7 + random.uniform(-0.5, 0.5), 2),
                     "snd_buf_pkts": int(30 + 20 * random.random()), "snd_buf_ms": int(25 + 15 * random.random()), "retrans_total": 412, "loss_total": 37,
                     "drop_total": 0, "sent_total": 90210, "retrans_pct": 0.46, "loss_pct": 0.04}}
        return out

    def finish(self, cores, freqs, temp, total_kb, avail_kb, rates, blocked, fan_pwm=None, blocked_names=None):
        cpu_avg = round(sum(cores) / len(cores), 1) if cores else None
        cpu_max = max(cores) if cores else None
        mem_used = round(100.0 * (1 - avail_kb / total_kb), 1) if total_kb else None
        alerts = []
        if temp is not None and temp >= LIMITS["temp_crit"]:
            alerts.append({"level": "crit", "text": f"Temperatur {temp:.0f} °C"})
        elif temp is not None and temp >= LIMITS["temp_warn"]:
            alerts.append({"level": "warn", "text": f"Temperatur {temp:.0f} °C"})
        if cpu_avg is not None and cpu_avg >= LIMITS["cpu_warn"]:
            alerts.append({"level": "warn", "text": f"CPU-Last {cpu_avg:.0f} %"})
        elif cpu_max is not None and cpu_max >= LIMITS["core_warn"]:
            alerts.append({"level": "warn", "text": f"Ein CPU-Kern ist fast voll ({cpu_max:.0f} %)"})
        if mem_used is not None and mem_used >= LIMITS["mem_warn"]:
            alerts.append({"level": "warn", "text": f"RAM {mem_used:.0f} % belegt"})
        if blocked > 0:
            alerts.append({"level": "warn", "text": f"{blocked} Prozess(e) blockiert (D-State)", "kind": "dstate", "names": list(blocked_names or [])})
        return {"time": int(time.time()), "demo": self.demo,
                "cpu": {"cores": cores, "avg": cpu_avg, "max": cpu_max, "freq_mhz": freqs},
                "temp_c": None if temp is None else round(temp, 1),
                "fan_pct": None if fan_pwm is None else round(100.0 * fan_pwm / 255),   # Ansteuerung, keine Drehzahl
                "mem": {"used_pct": mem_used, "avail_mb": avail_kb // 1024,
                        "total_mb": total_kb // 1024},
                "net": rates, "procs_blocked": blocked, "alerts": alerts,
                # Platzhalter bis die Pipeline echte Werte liefert
                "encoder": {"fps": 29.9, "bitrate_mbit": 9.8, "max_mbit": 12}
                if self.demo else None}


IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def lan_ip():
    """IPv4-Adresse des Standard-Netzwerkwegs (für Adressen, die Kameras nutzen)."""
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("203.0.113.1", 9))  # sendet nichts, wählt nur den Weg
        ip = sk.getsockname()[0]
        sk.close()
        return ip
    except OSError:
        return "127.0.0.1"


NET_LABELS = {}   # Namen ergeben sich aus der Art der Karte (siehe net_label), nicht aus festen Annahmen über die Box


def net_label(name):
    """Anzeigename einer Netzwerkkarte: WLAN, USB-Router oder Ethernet."""
    if name.startswith(("wl",)):
        return f"WLAN ({name})"
    try:
        if "/usb" in os.path.realpath(f"/sys/class/net/{name}/device"):
            return f"USB-Router ({name})"
    except OSError:
        pass
    return f"Ethernet ({name})"


FIXED_ALIAS = {"eth1": "192.168.80.50"}   # feste Zweitadressen (setzt pipbox-net.service)


def fixed_ip(name):
    """Feste Zweitadresse einer Schnittstelle, wenn sie dort gerade gesetzt ist."""
    want = FIXED_ALIAS.get(name)
    if not want:
        return None
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", name],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return want if f" {want}/" in out else None


_TTL = {}


def ttl_cached(key, ttl, fn):
    """Ergebnis von fn() für ttl Sekunden merken. Spart Programmstarts und Dateilesen bei den vielen Abfragen der Oberfläche."""
    now = time.monotonic()
    hit = _TTL.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _TTL[key] = (now, val)
    return val


def ttl_cached_drop(key):
    _TTL.pop(key, None)


def iface_ips():
    """IPv4-Adressen der Netzwerkschnittstellen, 3 s zwischengespeichert (jede Abfrage startete sonst `ip`)."""
    return [dict(x) for x in ttl_cached("iface_ips", 3.0, _iface_ips_raw)]


def _iface_ips_raw():
    """IPv4-Adressen der Netzwerkschnittstellen (nur Linux), ohne lo/Container."""
    try:
        import fcntl
        import struct
        names = sorted(os.listdir("/sys/class/net"))
    except (ImportError, OSError):
        return []
    out = []
    for name in names:
        if name == "lo" or name.startswith(("docker", "veth", "br-", "virbr", "p2p", "tailscale", "tun", "tap", "wg", "zt")):
            continue          # virtuelle Netze (Tailscale, Container, VPN) sind keine Sendewege und keine Kameranetze
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ip = socket.inet_ntoa(fcntl.ioctl(sk.fileno(), 0x8915,
                                              struct.pack("256s", name[:15].encode()))[20:24])
            sk.close()
        except OSError:
            continue
        out.append({"iface": name, "ip": ip, "cam_ip": fixed_ip(name) or ip, "label": NET_LABELS.get(name) or net_label(name)})
    return out


LINKS_FILE = "/run/pipbox-send/srtla-links.txt"


def uplink_states(selected):
    """Ampel je Sendeweg aus der Datei des Senders (alle paar Sekunden eine Zeile je Weg): {Schnittstelle: {"state", "srtt"}}.
    "an" = der Weg trägt Pakete, "reserve" = verbunden, wird aber nicht genutzt (Laufzeit zu hoch oder zu unruhig), "aus" = nicht
    verbunden oder ohne Netz. Leeres Ergebnis, wenn keine frischen Daten da sind (Sendung aus)."""
    try:
        if time.time() - os.stat(LINKS_FILE).st_mtime > 20:
            return {}
        with open(LINKS_FILE) as f:
            lines = f.readlines()[-60:]
    except OSError:
        return {}
    last = {}
    for ln in lines:
        m = re.match(r"\S+ links: (\S+) srtt=(-?\d+)ms .*? (genutzt|reserve) ", ln)
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


class NetChoice:
    """Welches Netzwerk die Kameras zur Box nutzen (bestimmt die RTMP-Adresse)."""

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
        return lan_ip()   # Standardweg, wenn nichts gewählt oder Schnittstelle weg

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


HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


class SrtlaStore:
    """SRTLA-Server (Name, Adresse, Port, Stream-ID), Auswahl und Sendeeinstellungen.

    Gespeichert in <state>/srtla.json. Eine Liste mit Dropdown ist das, was die
    Original-BELABOX nicht kann. Die Werte werden hier streng geprüft, denn der
    spätere Sende-Dienst läuft mit Root-Rechten und liest dieselbe Datei.
    """
    DEFAULT_SETTINGS = {"min_kbps": 300, "max_kbps": 12000, "latency_ms": 4000, "uplinks": ["eth0", "eth1"],
                        "spread": "best"}

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"servers": [], "selected": None, "settings": dict(self.DEFAULT_SETTINGS)}
        try:
            with open(path) as f:
                loaded = json.load(f)
            self.data.update({k: loaded[k] for k in ("servers", "selected") if k in loaded})
            self.data["settings"].update(loaded.get("settings", {}))
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def check(req):
        name = str(req.get("name", "")).strip()
        host = str(req.get("host", "")).strip().lower()
        sid = str(req.get("streamid", "")).strip()
        try:
            port = int(req.get("port"))
        except (TypeError, ValueError):
            raise ValueError("Port: Zahl von 1 bis 65535")
        if not 1 <= len(name) <= 40:
            raise ValueError("Name: 1 bis 40 Zeichen")
        if not HOST_RE.match(host):
            raise ValueError("Adresse: nur Buchstaben, Ziffern, Punkt und Bindestrich (ohne rtmp:// oder Leerzeichen)")
        if not 1 <= port <= 65535:
            raise ValueError("Port: Zahl von 1 bis 65535")
        if len(sid) > 512 or any(ord(c) < 32 or ord(c) > 126 for c in sid):
            raise ValueError("Stream-ID: höchstens 512 sichtbare Zeichen")
        return {"name": name, "host": host, "port": port, "streamid": sid}

    def public(self):
        """Ohne Stream-ID: sie ist ein Zugangsschlüssel und bleibt auf der Box."""
        with self.lock:
            return {"servers": [{"id": s["id"], "name": s["name"], "host": s["host"], "port": s["port"],
                                 "has_streamid": bool(s.get("streamid"))} for s in self.data["servers"]],
                    "selected": self.data["selected"], "settings": dict(self.data["settings"])}

    def add(self, req):
        item = self.check(req)
        with self.lock:
            item["id"] = secrets.token_hex(4)
            self.data["servers"].append(item)
            if not self.data["selected"]:
                self.data["selected"] = item["id"]
            self.save()
        return item["id"]

    def update(self, sid, req):
        with self.lock:
            cur = next((s for s in self.data["servers"] if s["id"] == sid), None)
            if not cur:
                raise KeyError(sid)
            merged = {"name": cur["name"], "host": cur["host"], "port": cur["port"],
                      "streamid": cur.get("streamid", "")}
            merged.update({k: v for k, v in req.items() if k in merged and not (k == "streamid" and v in (None, ""))})
            if req.get("clear_streamid"):
                merged["streamid"] = ""
            cur.update(self.check(merged))
            self.save()

    def remove(self, sid):
        with self.lock:
            n = len(self.data["servers"])
            self.data["servers"] = [s for s in self.data["servers"] if s["id"] != sid]
            if len(self.data["servers"]) == n:
                raise KeyError(sid)
            if self.data["selected"] == sid:
                self.data["selected"] = self.data["servers"][0]["id"] if self.data["servers"] else None
            self.save()

    def select(self, sid):
        with self.lock:
            if not any(s["id"] == sid for s in self.data["servers"]):
                raise ValueError("Server nicht gefunden")
            self.data["selected"] = sid
            self.save()

    def set_settings(self, req, valid_ifaces):
        with self.lock:
            cur = dict(self.data["settings"])
        # Fehlende Felder behalten ihren gespeicherten Wert (z. B. beim Anhaken eines Netzes wird nur die Netzliste geschickt),
        # sonst überschreibt ein veralteter Stand der Seite andere Einstellungen.
        try:
            mn = int(req.get("min_kbps", cur.get("min_kbps")))
            mx = int(req.get("max_kbps", cur.get("max_kbps")))
            lat = int(req.get("latency_ms", cur.get("latency_ms")))
        except (KeyError, TypeError, ValueError):
            raise ValueError("Bitrate und Latenz müssen Zahlen sein")
        if not 100 <= mn < mx <= 20000:
            raise ValueError("Bitrate: Minimum ab 100, kleiner als Maximum, Maximum höchstens 20000 kbit/s")
        if not 100 <= lat <= 10000:
            raise ValueError("Latenz: 100 bis 10000 ms")
        ups = [u for u in req.get("uplinks", []) if isinstance(u, str)]
        if any(not re.fullmatch(r"[A-Za-z0-9._-]{1,15}", u) for u in ups):
            raise ValueError("Ungültiger Name eines Netzwerks")
        with self.lock:
            before = set(self.data["settings"].get("uplinks", []))
        # Neu angehakte Netze müssen es geben. Schon gespeicherte, die gerade fehlen (Router abgezogen), bleiben stehen,
        # sonst ließe sich nichts mehr ändern, solange ein altes Netz in der Liste hängt.
        if any(u not in valid_ifaces and u not in before for u in ups) or not any(u in valid_ifaces for u in ups):
            raise ValueError("Mindestens ein vorhandenes Netzwerk als Sendeweg wählen")
        spread = req.get("spread", cur.get("spread", "best"))
        if spread not in ("best", "all"):
            raise ValueError("Verteilung: beste Leitung bevorzugen oder alle gleichzeitig")
        with self.lock:
            self.data["settings"] = {"min_kbps": mn, "max_kbps": mx, "latency_ms": lat, "uplinks": sorted(set(ups)),
                                     "spread": spread}
            self.save()


PIP_CORNERS = ("oben links", "oben rechts", "unten links", "unten rechts", "unten Mitte", "frei (verschiebbar)")
FREE = 5                      # Nummer von "frei": Position aus x/y (Promille)
PLUGIN_SO = "/opt/pipbox/gst/libgstpbpip.so"
SEND_STATUS = "/run/pipbox-send/status.json"
VIEW_FILE = "main-view"                 # Datei im Zustandsordner: "ausgeblendet Tonquelle stumm" (liest der Baustein pbctl, siehe gst/gstpbpip.c)
VIEW_STATE = "/run/pipbox-send/view-state"   # hier meldet pbctl zurück, was wirklich gilt
SWAP_SELECT = "main-select"            # Datei im Zustandsordner: welche Kamera ist Hauptbild (liest der Baustein pbctl)
SWAP_STATE = "/run/pipbox-send/swap-state"   # hier meldet pbctl zurück, was er eingestellt hat
_CENTER = {"mtime": None, "ok": True, "free": True}


def pip_corners():
    """Wählbare Positionen. "unten Mitte" und "frei" nur, wenn der installierte Baustein sie kennt (sonst landet das Bild oben links)."""
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _CENTER["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _CENTER.update(mtime=mt, ok=b"4 unten Mitte" in data, free=b"5 frei" in data)
    except OSError:
        return PIP_CORNERS          # kein Baustein (Entwicklungsrechner, Demo): alle anzeigen
    if not _CENTER["ok"]:
        return PIP_CORNERS[:4]
    return PIP_CORNERS if _CENTER["free"] else PIP_CORNERS[:5]


_MULTI = {"mtime": None, "ok": True}


def plugin_multi():
    """Kennt der installierte Baustein den gemeinsamen Mischer für bis zu drei kleine Bilder (Eigenschaft "slot3")?
    Ein alter Baustein (z. B. wenn der Neubau beim Update scheiterte) bekommt die alte Form mit zwei Mischern."""
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _MULTI["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                _MULTI.update(mtime=mt, ok=b"slot3" in f.read())
    except OSError:
        return True
    return _MULTI["ok"]


_SWAP = {"mtime": None, "ok": True}


def plugin_swap():
    """Kennt der installierte Baustein den Umschalter für den Tausch ohne Unterbrechung (pbpipsel)? Ohne Baustein (Entwicklungsrechner) ja."""
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _SWAP["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _SWAP.update(mtime=mt, ok=b"pbpipsel" in data and b"follow-tag" in data)
    except OSError:
        return True
    return _SWAP["ok"]


_FILL = {"mtime": None, "ok": True}
FEED_PORT = 9410            # "alle Kameras immer bereit": Zubringer je Platz (0 bis 3) geben Bild an Port FEED_PORT + 2*Platz und Ton an +1 weiter (nur Loopback)
FEED_VIDEO_CAPS = "application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000"
FEED_AUDIO_CAPS = "audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved"
FEED_FILL_VIDEO = "video/x-raw,format=NV12,width=1920,height=1080,framerate=30/1"
CAM_LIVE = "/run/pipbox-send/cam-live"       # pbctl meldet hier, welche Plätze gerade Bilder liefern ("1 0 1 0")


def plugin_fill():
    """Kann der installierte Baustein den Ausgang bei fehlendem Eingang füllen (Eigenschaft fill-caps von pbpipsel)? Ohne Baustein (Entwicklungsrechner) ja."""
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _FILL["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _FILL.update(mtime=mt, ok=b"fill-caps" in data and b"live-file" in data)
    except OSError:
        return True
    return _FILL["ok"]


def feeder_tool():
    """Pfad von gst-launch-1.0 (Paket gstreamer1.0-tools) oder None: Die Zubringer von "alle Kameras immer bereit" brauchen es. Ein frisches BELABOX-Image bringt es nicht mit."""
    return shutil.which("gst-launch-1.0")


_VIEW = {"mtime": None, "ok": True}
VIEW_ENABLED = True          # Ansicht im Betrieb (Issue #19): Fußleiste am Handy; ohne passenden Baustein (plugin_view) bleibt es beim Neustart


def plugin_view():
    """Kennt der installierte Baustein die Ansicht im Betrieb (Eigenschaften "view-file" und "view-state-file" von pbctl: kleine Bilder
    ein-/ausblenden, Tonquelle, stumm ohne Neustart), und ist sie eingeschaltet (VIEW_ENABLED)? Ohne Baustein (Entwicklungsrechner) ja."""
    if not VIEW_ENABLED:
        return False
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _VIEW["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _VIEW.update(mtime=mt, ok=b"view-file" in data and b"view-state-file" in data)
    except OSError:
        return True
    return _VIEW["ok"]


_STYLE = {"mtime": None, "ok": True}


def plugin_style():
    """Kennt der installierte Baustein Beschnitt, Deckkraft und Rahmen je kleinem Bild (Eigenschaften "style1" bis "style3")?
    Ein alter Baustein (z. B. wenn der Neubau beim Update scheiterte) würde die Eigenschaft nicht kennen und die Sendekette
    nicht starten: dann bleibt sie aus dem Pipeline-Text weg. Ohne Baustein (Entwicklungsrechner) ja."""
    try:
        mt = os.stat(PLUGIN_SO).st_mtime
        if _STYLE["mtime"] != mt:
            with open(PLUGIN_SO, "rb") as f:
                data = f.read()
            _STYLE.update(mtime=mt, ok=b"style1" in data and b"style3" in data)
    except OSError:
        return True
    return _STYLE["ok"]


# Aussehen der kleinen Bilder (Deckkraft, Beschnitt, Rahmen), je Stelle 1 bis 3 im Bild-in-Bild. Beschnitt in Pixeln eines Bildes von
# 1920 x 1080 (so groß ist die Bezugsgröße jeder Kamera; der Baustein rechnet auf die verkleinerte Größe um), Rahmenbreite und Rundung
# in Pixeln eines 1920 Pixel breiten Hauptbildes.
STYLE_SLOTS = ("1", "2", "3")
CROP_REF_W, CROP_REF_H, CROP_KEEP = 1920, 1080, 32          # Bezugsgröße des Beschnitts, so viele Pixel bleiben mindestens stehen
STYLE_DEFAULT = {"visible": True, "opacity": 100, "crop": {"l": 0, "r": 0, "t": 0, "b": 0}, "radius": 0,
                 "border": {"enabled": False, "width": 6, "color": "#ffffff", "opacity": 100}}
STYLE_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _style_copy(st):
    out = {"visible": st["visible"], "opacity": st["opacity"], "crop": dict(st["crop"]), "radius": st.get("radius", 0), "border": dict(st["border"])}
    if "radius" not in st:                      # ältere Einstellung: Die Rundung gehörte zum Rahmen und wirkte nur mit ihm
        out["radius"] = int(out["border"].get("radius", 0)) if out["border"].get("enabled") else 0
    out["border"].pop("radius", None)
    return out


def clean_style(src, old=None, strict=False):
    """Eine Stileinstellung prüfen und ergänzen. strict (Anfrage der Oberfläche): Fehler werden gemeldet. Sonst (gespeicherte Datei,
    pipeline.json gehört dem Benutzer pipbox, build() läuft auch als root): alles wird in feste Bereiche gezwungen, es gelangt nie
    ein Text in den Pipeline-Text. old: bisherige Werte für fehlende Felder."""
    st = _style_copy(old if isinstance(old, dict) and "crop" in old and "border" in old else STYLE_DEFAULT)
    if src is None:
        src = {}
    if not isinstance(src, dict):
        if strict:
            raise ValueError("Aussehen des kleinen Bildes: ungültige Angabe")
        src = {}

    def num(v, lo, hi, cur, what):
        if isinstance(v, bool) or not isinstance(v, (int, float)) and not (isinstance(v, str) and v.strip().lstrip("-").isdigit()):
            if strict:
                raise ValueError(f"{what} muss eine Zahl sein")
            return cur
        n = int(float(v))
        if strict and not lo <= n <= hi:
            raise ValueError(f"{what}: {lo} bis {hi}")
        return max(lo, min(hi, n))
    if "visible" in src:
        if isinstance(src["visible"], bool):
            st["visible"] = src["visible"]
        elif strict:
            raise ValueError("Kleines Bild zeigen: ja oder nein")
    if "opacity" in src:
        st["opacity"] = num(src["opacity"], 0, 100, st["opacity"], "Deckkraft")
    crop = src.get("crop")
    if isinstance(crop, dict):
        for k, lbl in (("l", "links"), ("r", "rechts"), ("t", "oben"), ("b", "unten")):
            if k in crop:
                st["crop"][k] = num(crop[k], 0, 1900, st["crop"][k], f"Beschnitt {lbl}") // 2 * 2     # gerade: Chroma ist halb so groß
    elif crop is not None and strict:
        raise ValueError("Beschnitt: ungültige Angabe")
    c = st["crop"]
    for a, b, total in (("l", "r", CROP_REF_W), ("t", "b", CROP_REF_H)):
        room = total - CROP_KEEP
        if c[a] + c[b] > room:
            if strict:
                raise ValueError(f"Beschnitt: {'links plus rechts' if a == 'l' else 'oben plus unten'} höchstens {room} Pixel "
                                 f"(es bleiben mindestens {CROP_KEEP} Pixel stehen)")
            k = room / float(c[a] + c[b])
            c[a], c[b] = int(c[a] * k) // 2 * 2, int(c[b] * k) // 2 * 2
    if "radius" in src:
        st["radius"] = num(src["radius"], 0, 60, st["radius"], "Eckenrundung")
    border = src.get("border")
    if isinstance(border, dict):
        b = st["border"]
        if "enabled" in border:
            if isinstance(border["enabled"], bool):
                b["enabled"] = border["enabled"]
            elif strict:
                raise ValueError("Rahmen: ein oder aus")
        if "width" in border:
            b["width"] = num(border["width"], 1, 40, b["width"], "Rahmendicke")
        if "color" in border:
            if isinstance(border["color"], str) and STYLE_COLOR_RE.match(border["color"]):
                b["color"] = border["color"].lower()
            elif strict:
                raise ValueError("Rahmenfarbe: Farbe wie #ffffff")
        if "opacity" in border:
            b["opacity"] = num(border["opacity"], 10, 100, b["opacity"], "Rahmendeckkraft")
        if "radius" in border:                                                  # ältere Anfrage: die Rundung stand im Rahmen
            legacy = num(border["radius"], 0, 60, st["radius"], "Eckenrundung")
            if "radius" not in src and b["enabled"]:
                st["radius"] = legacy
    elif border is not None and strict:
        raise ValueError("Rahmen: ungültige Angabe")
    return st


def clean_styles(src, old=None, strict=False):
    """Die Stile der Stellen 1 bis 3 (Schlüssel "1", "2", "3")."""
    if src is not None and not isinstance(src, dict):
        if strict:
            raise ValueError("Aussehen der kleinen Bilder: ungültige Angabe")
        src = None
    old = old if isinstance(old, dict) else {}
    return {k: clean_style((src or {}).get(k), old.get(k), strict) for k in STYLE_SLOTS}


def style_text(st):
    """Eigenschaft style<N> des Bausteins (Format siehe gst/gstpbpip.c), nur mit Werten, die vom Standard abweichen; leer, wenn alles
    Standard ist. st ist eine geprüfte Stileinstellung (clean_style): nur Zahlen und eine Hex-Farbe gelangen in den Text."""
    parts = []
    op = st["opacity"] if st["visible"] else 0
    if op != 100:
        parts.append(f"op={int(op)}")
    for key, name in (("l", "cl"), ("r", "cr"), ("t", "ct"), ("b", "cb")):
        if st["crop"][key]:
            parts.append(f"{name}={int(st['crop'][key])}")
    b = st["border"]
    if b["enabled"]:
        parts.append(f"bw={int(b['width'])}")
        parts.append(f"bc={b['color'][1:]}")
        if b["opacity"] != 100:
            parts.append(f"bo={int(b['opacity'])}")
    if st["radius"]:                                                     # die Rundung gilt für das Bild selbst, mit oder ohne Rahmen
        parts.append(f"br={int(st['radius'])}")
    return ",".join(parts)


SIZE_MIN, SIZE_MAX = 1, 100       # Skalierung eines kleinen Bildes in Prozent des Hauptbildes (100 = so groß wie das Hauptbild)
HW_MIN_W, HW_MIN_H = 128, 72     # kleinste Größe, auf die der Hardware-Decoder verkleinert: auf der Box gemessen geht es ab 120 Pixeln Breite (Verhältnis 1:16), darunter nicht


def pip_size(pct):
    """Breite/Höhe des kleinen Bildes in Pixel (gerade, durch 16 teilbar in der Breite)."""
    w = max(16, int(round(1920 * pct / 100.0 / 16.0)) * 16)
    h = int(round(w * 9 / 16.0 / 2.0)) * 2
    return w, h


def small_decode(w, h):
    """Der Teil der Pipeline, der ein kleines Bild auf w x h bringt: Der Hardware-Decoder verkleinert selbst. Reicht seine Verkleinerung nicht (unter
    128 Pixel Breite), verkleinert er auf 128 x 72 und eine Software-Stufe (videoscale) macht den Rest; bei dieser Größe kostet sie fast nichts."""
    if w >= HW_MIN_W:
        return f"mppvideodec width={w} height={h} !\nvideo/x-raw,format=NV12 !\n"
    return (f"mppvideodec width={HW_MIN_W} height={HW_MIN_H} !\nvideoscale !\nvideo/x-raw,format=NV12,width={w},height={h} !\n")


DEFAULT_MAIN_DELAY_MS = 450   # Ausgangswert (Schätzung, per Regler anpassbar): das Hauptbild wartet so lange, damit die kleinen Bilder zeitlich passen
DEFAULT_PIP_DELAY_MS = 0      # kleine Bilder: zusätzliche Wartezeit je Bild (Feinabgleich, wenn eine Kamera mehr hinterherhinkt)
DELAY_KEYS = ("main_delay_ms", "pip_delay_ms", "pip2_delay_ms", "pip3_delay_ms")
FRAME_MS = 33                 # eine Warteschlange gibt erst nach der Schwelle frei: beim Bild ein Bild zugeben


PENDING_LABELS = (("server", "SRTLA-Server"), ("bitrate", "Bitrate"), ("latency", "Latenz"), ("spread", "Verteilung"))


def srtla_signature(data):
    """Kurze Prüfsummen der Einstellungen, die erst beim Start der Sendekette wirken (ohne die Stream-ID preiszugeben).
    Die Sendekette merkt sich ihre Werte beim Start; weichen die gespeicherten davon ab, ist ein Neustart nötig."""
    cur = next((x for x in data.get("servers", []) if x.get("id") == data.get("selected")), None) or {}
    st = {**SrtlaStore.DEFAULT_SETTINGS, **data.get("settings", {})}

    def h(*a):
        return hashlib.sha256(json.dumps(a, sort_keys=True).encode()).hexdigest()[:12]
    try:
        mn, mx, lat = int(st["min_kbps"]), int(st["max_kbps"]), int(st["latency_ms"])
    except (TypeError, ValueError):
        mn = mx = lat = 0
    return {"server": h(cur.get("id"), cur.get("host"), cur.get("port"), cur.get("streamid")),
            "bitrate": h(mn, mx), "latency": h(lat), "spread": h("all" if st.get("spread") == "all" else "best")}


class PipelineStore:
    """Welche Pipeline gesendet wird und welche Kameras sie nutzt.

    Typ "single": eine Kamera. Typ "pip": Hauptbild plus kleines Bild. Die Pipeline
    ist ein Text für belacoder, abgeleitet von BELABOX' Standard-Pipeline für RTMP
    (h265_rtmp_localhost_publish_live_30fps). Das kleine Bild verkleinert der
    Hardware-Decoder selbst; unser Baustein schreibt es nur in das Hauptbild
    (lesen wäre auf diesem Chip zu langsam).
    """
    DEFAULT = {"type": "single", "main": "", "pip": "", "corner": 3, "size_pct": 25, "size_pct2": 25, "size_pct3": 25, "audio": "main",
               "pip2": "", "corner2": 2, "pip3": "", "corner3": 0, "x": 500, "y": 500, "x2": 500, "y2": 500, "x3": 500, "y3": 500, "main_delay_ms": DEFAULT_MAIN_DELAY_MS,
               "pip_delay_ms": DEFAULT_PIP_DELAY_MS, "pip2_delay_ms": DEFAULT_PIP_DELAY_MS,
               "pip3_delay_ms": DEFAULT_PIP_DELAY_MS, "auto_failover": True, "swap_cams": 0, "always_ready": False}
    Q = "queue max-size-time=10000000000 max-size-buffers=1000 max-size-bytes=41943040"

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.cfg = dict(self.DEFAULT)
        try:
            with open(path) as f:
                self.cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
        self.cfg["styles"] = clean_styles(self.cfg.get("styles"))        # ältere Dateien kennen sie noch nicht

    def save(self):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as f:
            json.dump(self.cfg, f, indent=1)
        os.replace(tmp, self.path)
        self.write_delay_file()

    def write_delay_file(self):
        """Wert für die laufende Sendekette (liest unser Baustein pbctl alle 0,3 s). Atomar ersetzen. Läuft die Sendekette im Tausch-Betrieb,
        gilt die Reihenfolge der Kameras beim Aufbau (die Verzögerung gehört zur Kamera, nicht zum Platz)."""
        if self.path == os.devnull:
            return
        d = os.path.join(os.path.dirname(os.path.abspath(self.path)), "main-delay-ms")
        try:
            base = None
            try:
                with open(SEND_STATUS) as f:
                    base = (json.load(f).get("swap") or {}).get("cams")
            except (OSError, ValueError, AttributeError):
                pass
            vals = self.delay_values(self.cfg, base if isinstance(base, list) else None)
            with open(d + ".tmp", "w") as f:
                f.write(" ".join(map(str, vals)) + "\n")
            os.chmod(d + ".tmp", 0o644)
            os.replace(d + ".tmp", d)
        except OSError:
            pass

    SLOT_DELAY = {"pip": "pip_delay_ms", "pip2": "pip2_delay_ms", "pip3": "pip3_delay_ms"}

    def swap_main_pip(self, with_key=None):
        """Hauptbild gegen eine Kamera tauschen, die gerade als kleines Bild im Bild ist (Szenenwechsel, Stufe 1). Ohne Angabe
        das erste kleine Bild. Kamera und Verzögerung bleiben beisammen (die Verzögerung gehört zur Kamera); Ecke, Größe,
        Position und die Wahl des Tons (Hauptbild oder kleines Bild) bleiben am Platz. Wahr, wenn dabei ein ausgeblendetes
        kleines Bild eingeblendet wurde."""
        with self.lock:
            c = self.cfg
            if c.get("type") != "pip" or not c.get("pip") or not c.get("main"):
                raise ValueError("Zum Tauschen braucht es ein Hauptbild und ein kleines Bild")
            slot = "pip"
            if with_key:
                slot = next((k for k in self.SLOT_DELAY if c.get(k) == with_key), None)
                if slot is None:
                    raise ValueError("Diese Kamera ist gerade nicht als kleines Bild im Bild")
            dk = self.SLOT_DELAY[slot]
            c["main"], c[slot] = c[slot], c["main"]
            c["main_delay_ms"], c[dk] = c.get(dk, 0), c.get("main_delay_ms", 0)
            if c.get("inactive"):                                                    # die Hauptkamera ist nie deaktiviert
                rest = [k for k in c["inactive"] if k != c["main"]]
                if rest:
                    c["inactive"] = rest
                else:
                    c.pop("inactive")
            # "Ausgeblendet" gehört zur Kamera: die bisherige Hauptkamera war sichtbar, also ist ihr kleines Bild nach dem Tausch sichtbar
            styles = clean_styles(c.get("styles"))
            idx = STYLE_SLOTS[list(self.SLOT_DELAY).index(slot)]
            shown = not styles[idx]["visible"]
            if shown:
                styles[idx]["visible"] = True
                c["styles"] = styles
            self.save()
            return shown

    def set(self, req, camera_keys):
        t = req.get("type")
        if t not in ("single", "pip"):
            raise ValueError("Art des Bildaufbaus unbekannt")
        main = str(req.get("main", ""))
        if main not in camera_keys:
            raise ValueError("Hauptkamera: bitte eine vorhandene Kamera wählen")
        cfg = {"type": t, "main": main, "pip": "", "corner": 3, "size_pct": 25, "size_pct2": 25, "size_pct3": 25, "audio": "main",
               "pip2": "", "corner2": 2, "pip3": "", "corner3": 0, "x": 500, "y": 500, "x2": 500, "y2": 500, "x3": 500, "y3": 500, "main_delay_ms": DEFAULT_MAIN_DELAY_MS,
               "pip_delay_ms": DEFAULT_PIP_DELAY_MS, "pip2_delay_ms": DEFAULT_PIP_DELAY_MS,
               "pip3_delay_ms": DEFAULT_PIP_DELAY_MS, "auto_failover": req.get("auto_failover", True) is not False, "swap_cams": 0,
               "always_ready": t == "pip" and req.get("always_ready") is True}
        cfg["styles"] = clean_styles(req.get("styles"), self.cfg.get("styles"), strict=True)       # Deckkraft, Beschnitt, Rahmen je kleinem Bild
        if t == "pip":
            try:
                swap = int(req.get("swap_cams", 0) or 0)
            except (TypeError, ValueError):
                raise ValueError("Tausch: aus, 2 oder 4 Kameras")
            if swap not in (0, 2, 4):
                raise ValueError("Tausch: aus, 2 oder 4 Kameras")
            cfg["swap_cams"] = swap
            pipk = str(req.get("pip", ""))
            if pipk not in camera_keys or pipk == main:
                raise ValueError("Kleines Bild: eine andere vorhandene Kamera wählen")
            try:
                corner, pct = int(req.get("corner", 3)), int(req.get("size_pct", 25))
                # Größe je kleinem Bild; eine Anfrage ohne die Werte für Bild 2 und 3 (älterer Client) gibt allen die Größe von Bild 1
                pct2, pct3 = int(req.get("size_pct2", pct)), int(req.get("size_pct3", pct))
            except (TypeError, ValueError):
                raise ValueError("Ecke und Größe müssen Zahlen sein")
            if corner not in range(len(pip_corners())) or not all(SIZE_MIN <= v <= SIZE_MAX for v in (pct, pct2, pct3)):
                raise ValueError("Position aus der Liste wählen, Skalierung jedes kleinen Bildes %d bis %d Prozent" % (SIZE_MIN, SIZE_MAX))
            audio = req.get("audio", "main")
            if audio not in ("main", "pip", "pip2", "pip3"):
                raise ValueError("Ton: Hauptbild oder eines der kleinen Bilder")
            cfg.update(pip=pipk, corner=corner, size_pct=pct, size_pct2=pct2, size_pct3=pct3, audio=audio)
            for key in ("x", "y", "x2", "y2", "x3", "y3"):
                try:
                    val = int(req.get(key, 500))
                except (TypeError, ValueError):
                    raise ValueError("Position muss eine Zahl sein")
                cfg[key] = max(0, min(1000, val))
            for key, dflt in (("main_delay_ms", DEFAULT_MAIN_DELAY_MS), ("pip_delay_ms", DEFAULT_PIP_DELAY_MS),
                              ("pip2_delay_ms", DEFAULT_PIP_DELAY_MS), ("pip3_delay_ms", DEFAULT_PIP_DELAY_MS)):
                try:
                    delay = int(req.get(key, dflt)) if req.get(key) != "" else 0
                except (TypeError, ValueError):
                    raise ValueError("Verzögerung muss eine Zahl sein (Millisekunden)")
                if not 0 <= delay <= 3000:
                    raise ValueError("Verzögerung: 0 bis 3000 ms")
                cfg[key] = delay
            pip2 = str(req.get("pip2", "") or "")
            if pip2:
                try:
                    corner2 = int(req.get("corner2", 2))
                except (TypeError, ValueError):
                    raise ValueError("Ecke muss eine Zahl sein")
                if pip2 not in camera_keys or pip2 in (main, pipk):
                    raise ValueError("Zweites kleines Bild: eine weitere vorhandene Kamera wählen")
                if corner2 not in range(len(pip_corners())) or (corner2 == corner and corner != FREE):
                    raise ValueError("Zweites kleines Bild: eine andere Position als das erste wählen")
                cfg.update(pip2=pip2, corner2=corner2)
            else:
                cfg["pip2_delay_ms"] = 0
            pip3 = str(req.get("pip3", "") or "")
            if pip3 and pip2:
                try:
                    corner3 = int(req.get("corner3", 0))
                except (TypeError, ValueError):
                    raise ValueError("Ecke muss eine Zahl sein")
                if pip3 not in camera_keys or pip3 in (main, pipk, pip2):
                    raise ValueError("Drittes kleines Bild: eine weitere vorhandene Kamera wählen")
                if corner3 not in range(len(pip_corners())) or (corner3 != FREE and corner3 in (corner, cfg["corner2"])):
                    raise ValueError("Drittes kleines Bild: eine andere Position als die ersten beiden wählen")
                cfg.update(pip3=pip3, corner3=corner3)
            else:
                cfg["pip3_delay_ms"] = 0
            if cfg["audio"] == "pip2" and not cfg["pip2"] or cfg["audio"] == "pip3" and not cfg["pip3"]:
                raise ValueError("Ton: dieses kleine Bild ist nicht gewählt")
        pips = {cfg.get(k) for k in ("pip", "pip2", "pip3")} - {""}
        old = req.get("inactive") if "inactive" in req else self.cfg.get("inactive", [])
        inactive = list(dict.fromkeys(k for k in (old if isinstance(old, list) else []) if isinstance(k, str) and k in pips))[:3]
        if t == "pip" and inactive:                                                 # deaktivierte Kameras (Issue #19); ohne sie fehlt der Schlüssel
            cfg["inactive"] = inactive
        with self.lock:
            self.cfg = cfg
            self.save()

    def set_active(self, key, active):
        """Eine Kamera deaktivieren (sie springt bei Ausfall des Hauptbildes nicht als Ersatz ein) oder wieder aktivieren. Nur kleine Bilder."""
        if not isinstance(active, bool) or not isinstance(key, str) or not KEY_RE.match(key):
            raise ValueError("Kamera oder Zustand ungültig")
        with self.lock:
            c = self.cfg
            if c.get("type") != "pip":
                raise ValueError("Das gibt es nur bei der Art Bild-in-Bild")
            if key == c.get("main"):
                raise ValueError("Das Hauptbild lässt sich nicht deaktivieren")
            if key not in (c.get("pip"), c.get("pip2"), c.get("pip3")):
                raise ValueError("Diese Kamera ist nicht im Bild")
            cur = [k for k in (c.get("inactive") or []) if k != key]
            if not active:
                cur.append(key)
            if cur:
                c["inactive"] = cur
            else:
                c.pop("inactive", None)
            self.save()

    @classmethod
    def _safe_cfg(cls, c):
        """Zahlenfelder zu Zahlen in festen Bereichen zwingen. build() läuft über pipbox_send auch als root, und die
        Datei pipeline.json gehört dem Benutzer pipbox: ein eingeschleuster Text darf nie in den Pipeline-Text gelangen."""
        out = dict(c)

        def num(k, lo, hi):
            try:
                v = int(out.get(k, cls.DEFAULT[k]))
            except (TypeError, ValueError):
                v = cls.DEFAULT[k]
            out[k] = max(lo, min(hi, v))
        for k in ("corner", "corner2", "corner3"):
            num(k, 0, len(PIP_CORNERS) - 1)
        num("size_pct", SIZE_MIN, SIZE_MAX)
        for k in ("size_pct2", "size_pct3"):                  # ältere Dateien kennen sie nicht: dann gilt die Größe von Bild 1
            if k not in out:
                out[k] = out["size_pct"]
            num(k, SIZE_MIN, SIZE_MAX)
        for k in ("x", "y", "x2", "y2", "x3", "y3"):
            num(k, 0, 1000)
        for k in ("main_delay_ms", "pip_delay_ms", "pip2_delay_ms", "pip3_delay_ms"):
            num(k, 0, 3000)
        num("swap_cams", 0, 4)
        if out["swap_cams"] not in (2, 4):
            out["swap_cams"] = 0
        out["always_ready"] = out.get("always_ready") is True and out.get("type") == "pip"
        out["type"] = "pip" if out.get("type") == "pip" else "single"
        out["styles"] = clean_styles(out.get("styles"))
        if "inactive" in out:
            ina = out["inactive"]
            out["inactive"] = [k for k in ina if isinstance(k, str) and KEY_RE.match(k)][:3] if isinstance(ina, list) else []
        return out

    @classmethod
    def _layout(cls, c):
        """Welche kleinen Bilder wirklich dabei sind: (pip, pip2, pip3, multi). c ist eine geprüfte Einstellung (_safe_cfg)."""
        pip = c["type"] == "pip" and bool(KEY_RE.match(c.get("pip", "")))
        pip2 = bool(pip and c.get("pip2") and KEY_RE.match(c["pip2"]) and c.get("corner2") in range(len(PIP_CORNERS))
                    and (c["corner2"] != c["corner"] or c["corner"] == FREE))
        multi = plugin_multi()
        pip3 = bool(multi and pip2 and c.get("pip3") and KEY_RE.match(c["pip3"])
                    and c.get("corner3") in range(len(PIP_CORNERS)) and (c["corner3"] == FREE or c["corner3"] not in (c["corner"], c["corner2"])))
        return pip, pip2, pip3, multi

    @classmethod
    def swap_plan(cls, cfg):
        """Tausch ohne Unterbrechung (Hauptbild gegen eine Kamera tauschen, ohne den Encoder neu zu starten): Plan oder None.
        Jede Kamera der Tauschgruppe bekommt zwei Zweige (groß und klein), ein Umschalter wählt das Hauptbild. Gruppe: Hauptbild und
        erstes kleines Bild (swap_cams = 2) oder alle (4). cams: Kameras in der Reihenfolge des Aufbaus (Hauptbild, kleine Bilder 1 bis 3);
        state/line: Anfangszustand (Hauptkamera, dann Kamera an Stelle 1 bis 3, 15 = keine), audio_pos: -1 Ton folgt dem Hauptbild,
        0 bis 2 Ton der Kamera an dieser Stelle; asel: Ton läuft ebenfalls über einen Umschalter."""
        c = cls._safe_cfg(cfg)
        if c["type"] != "pip" or c["swap_cams"] not in (2, 4) or not KEY_RE.match(c.get("main", "")) or not plugin_swap():
            return None
        pip, pip2, pip3, multi = cls._layout(c)
        if not pip or not multi:
            return None
        cams = [c["main"], c["pip"]] + ([c["pip2"]] if pip2 else []) + ([c["pip3"]] if pip3 else [])
        if len(set(cams)) != len(cams):
            return None
        group = min(c["swap_cams"], len(cams))
        audio = c.get("audio", "main")
        if audio == "pip2" and not pip2 or audio == "pip3" and not pip3 or audio not in ("main", "pip", "pip2", "pip3"):
            audio = "main"
        pos = {"main": -1, "pip": 0, "pip2": 1, "pip3": 2}[audio]
        nums = [0, 1, 2 if pip2 else 15, 3 if pip3 else 15]
        return {"cams": cams, "group": group, "audio_pos": pos, "asel": pos < 0 or pos + 1 < group,
                "state": nums[0] | nums[1] << 4 | nums[2] << 8 | nums[3] << 12, "line": " ".join(map(str, nums))}

    @classmethod
    def always_plan(cls, cfg):
        """Plan für "alle Kameras immer bereit" (Issue #19) oder None. Jede Kamera hat einen Platz (0 = Hauptbild beim Aufbau, 1 bis 3 = kleine Bilder), an
        dem ein Zubringer (pipbox_always.py) ihren Stream aus dem RTMP-Server weiterreicht; die Sendekette selbst hat nie eine Quelle, die ausfällt. Alle Kameras
        sind in der Tauschgruppe und alle Töne laufen über den Ton-Umschalter. cams: die Kameras beim Aufbau; ein Kamerawechsel an einem Platz braucht keinen
        Neustart (der Zubringer holt dann den Stream der neuen Kamera)."""
        c = cls._safe_cfg(cfg)
        if pipbox_live.enabled(c) and KEY_RE.match(c.get("main", "")) and cls._layout(c)[0]:
            # Engine Compositor: keine Zubringer, kein pbpipsel; alle Änderungen gehen im Betrieb über den Steuerkanal von belacoder
            cams = [k for k in (c["main"], c.get("pip", ""), c.get("pip2", ""), c.get("pip3", "")) if k]
            if len(set(cams)) != len(cams):
                return None
            audio = c.get("audio", "main")
            pos = {"main": -1, "pip": 0, "pip2": 1, "pip3": 2}.get(audio, -1)
            return {"cams": cams, "group": len(cams), "audio_pos": pos, "asel": True, "always": True, "live": True, "state": 0x3210, "line": "0 1 2 3"}
        if c["type"] != "pip" or c.get("always_ready") is not True or not KEY_RE.match(c.get("main", "")) or not plugin_swap() or not plugin_fill() or not feeder_tool():
            return None
        pip, pip2, pip3, multi = cls._layout(c)
        if not pip:
            return None
        cams = [c["main"], c["pip"]] + ([c["pip2"]] if pip2 else []) + ([c["pip3"]] if pip3 else [])
        if len(set(cams)) != len(cams):
            return None
        audio = c.get("audio", "main")
        if audio == "pip2" and not pip2 or audio == "pip3" and not pip3 or audio not in ("main", "pip", "pip2", "pip3"):
            audio = "main"
        pos = {"main": -1, "pip": 0, "pip2": 1, "pip3": 2}[audio]
        nums = [0, 1, 2 if pip2 else 15, 3 if pip3 else 15]
        return {"cams": cams, "group": len(cams), "audio_pos": pos, "asel": True, "always": True,
                "state": nums[0] | nums[1] << 4 | nums[2] << 8 | nums[3] << 12, "line": " ".join(map(str, nums))}

    @classmethod
    def always_blocker(cls, cfg):
        """Warum "alle Kameras immer bereit" trotz Schalter nicht gilt (Text für die Anzeige), sonst None. Die Sendung läuft dann im normalen Modus weiter."""
        c = cls._safe_cfg(cfg)
        if c["type"] != "pip" or c.get("always_ready") is not True or cls.always_plan(cfg):
            return None
        if not feeder_tool():
            return "Alle Kameras immer bereit ist nicht aktiv: Das Programm gst-launch-1.0 fehlt (Paket gstreamer1.0-tools). Die Sendung läuft im normalen Modus. Ein Software-Update installiert das Paket."
        if not plugin_swap() or not plugin_fill():
            return "Alle Kameras immer bereit ist nicht aktiv: Der Überlagerungs-Baustein ist noch nicht auf dem neuen Stand. Die Sendung läuft im normalen Modus. Ein Software-Update baut ihn neu."
        return "Alle Kameras immer bereit ist nicht aktiv: Die Kameras des Bildaufbaus passen nicht dazu (jede Kamera nur einmal, mindestens ein kleines Bild). Die Sendung läuft im normalen Modus."

    AUDIO_POS = {"main": -1, "pip": 0, "pip2": 1, "pip3": 2}

    @classmethod
    def view_values(cls, cfg):
        """Ansicht im Betrieb aus der Einstellung: (ausgeblendet, Tonquelle). ausgeblendet: Bit 0 bis 2 = kleines Bild an Stelle 1 bis 3 ist
        ausgeblendet; Tonquelle: -1 Hauptbild, 0 bis 2 das kleine Bild an dieser Stelle (so, wie der Baustein es versteht)."""
        c = cls._safe_cfg(cfg)
        hide = 0
        for i, k in enumerate(STYLE_SLOTS):
            if not c["styles"][k]["visible"]:
                hide |= 1 << i
        plan = cls.swap_plan(c)
        audio = plan["audio_pos"] if plan else cls.AUDIO_POS.get(c.get("audio", "main"), -1)
        return hide, audio

    @classmethod
    def view_line(cls, cfg, mute=0):
        hide, audio = cls.view_values(cfg)
        return f"{hide} {audio} {1 if mute else 0}"

    def set_view(self, visible=None, audio=None):
        """Sichtbarkeit der kleinen Bilder (visible: {"1": bool, ...}) und Tonquelle (audio: main, pip, pip2, pip3) ändern und speichern.
        Gibt (ausgeblendet, Tonquelle) der neuen Einstellung zurück. Nur bei Bild-in-Bild."""
        with self.lock:
            c = self.cfg
            if c.get("type") != "pip":
                raise ValueError("Das gibt es nur bei der Art Bild-in-Bild")
            if visible is not None:
                if not isinstance(visible, dict) or any(k not in STYLE_SLOTS or not isinstance(v, bool) for k, v in visible.items()):
                    raise ValueError("Bild einblenden: Stelle 1 bis 3, ja oder nein")
                for k, v in visible.items():
                    if k == "2" and not c.get("pip2") or k == "3" and not c.get("pip3") or k == "1" and not c.get("pip"):
                        raise ValueError("Dieses kleine Bild gibt es nicht")
            if audio is not None:
                if not isinstance(audio, str) or audio not in self.AUDIO_POS:
                    raise ValueError("Ton: Hauptbild oder eines der kleinen Bilder")
                if audio == "pip" and not c.get("pip") or audio == "pip2" and not c.get("pip2") or audio == "pip3" and not c.get("pip3"):
                    raise ValueError("Ton: dieses kleine Bild gibt es nicht")
                if audio != "main" and c.get(audio) in (c.get("inactive") or []):
                    raise ValueError("Ton: eine deaktivierte Kamera kann nicht die Tonquelle sein")
            styles = clean_styles(c.get("styles"))
            for k, v in (visible or {}).items():
                styles[k]["visible"] = v
            c["styles"] = styles
            if audio is not None:
                c["audio"] = audio
            self.save()
            return self.view_values(c)

    @staticmethod
    def swap_line(slots, cams, group):
        """Umschaltzeile für die laufende Sendekette aus der Belegung (Hauptbild, kleine Bilder 1 bis 3; "" = leer) und den Kameras
        beim Aufbau. Der Tausch geht nur innerhalb der Gruppe; alles andere bleibt am Platz. None, wenn das nicht passt."""
        slots = [(s or "") for s in slots] + [""] * (4 - len(slots))
        if len(cams) < 2 or group < 2 or group > len(cams) or slots[len(cams):] != [""] * (4 - len(cams)):
            return None
        if sorted(slots[:group]) != sorted(cams[:group]):
            return None
        if any(slots[i] != cams[i] for i in range(group, len(cams))):
            return None
        return " ".join([str(cams.index(slots[0]))] + [str(cams.index(s)) if s else "15" for s in slots[1:]])

    @staticmethod
    def delay_values(cfg, base=None):
        """Verzögerungen in ms für die Steuerdatei, in der Reihenfolge der Kameras beim Aufbau der Sendekette (base: deren Schlüssel;
        sonst die Reihenfolge der Einstellung). Die Verzögerung gehört zur Kamera, nicht zum Platz."""
        if cfg.get("type") != "pip":
            return [0, 0, 0, 0]
        slots = ("main", "pip", "pip2", "pip3")
        by_key = {cfg[s]: cfg.get(d, 0) for s, d in zip(slots, DELAY_KEYS) if cfg.get(s)}
        order = list(base) if base else [cfg.get(s) for s in slots]
        vals = []
        for k in (order + [None] * 4)[:4]:
            try:
                vals.append(max(0, min(3000, int(by_key.get(k, 0) or 0))) if k else 0)
            except (TypeError, ValueError):
                vals.append(0)
        return vals

    def build(self, cfg=None, rtmp_port=1935, rtmp_app="publish"):
        """Pipeline-Text für belacoder. Die Schlüssel sind geprüft (a-z, 0-9, -, _)."""
        c = self._safe_cfg(cfg or self.cfg)
        if not KEY_RE.match(c.get("main", "")):
            return ""
        q = self.Q
        base = f"rtmp://127.0.0.1:{rtmp_port}/{rtmp_app}"
        pip, pip2, pip3, multi = self._layout(c)
        if pip and pipbox_live.enabled(c):
            return pipbox_live.build(c, base)

        def xy(cfg_, kx, ky, corner_):
            if corner_ != FREE:
                return ""
            return f" {kx}={int(cfg_.get(kx, 500))} {ky}={int(cfg_.get(ky, 500))}"
        sty = self._style_props(c)
        out = []
        # Hauptbild (samt Ton) verzögern: die kleinen Bilder treffen dann zeitlich besser auf das Hauptbild.
        # Die Wartezeit sitzt NACH dem Auspacken (dort haben die Pakete die Zeitstempel der Kamera) und gilt für
        # Bild und Ton gleich, damit beide zueinander synchron bleiben (gemessen: Abweichung unter einem Bild).
        delay = 0
        if c["type"] == "pip":
            try:
                delay = max(0, min(3000, int(c.get("main_delay_ms", DEFAULT_MAIN_DELAY_MS))))
            except (TypeError, ValueError):
                delay = 0
        qa = q + (f" min-threshold-time={delay * 1000000}" if delay else "")
        qv = q + (f" min-threshold-time={(delay + FRAME_MS) * 1000000}" if delay else "")
        if pip:
            qv, qa = qv + " name=mainq_v", qa + " name=mainq_a"

        def small(name, key):
            d = 0
            try:
                d = max(0, min(3000, int(c.get(key, DEFAULT_PIP_DELAY_MS) or 0)))
            except (TypeError, ValueError):
                d = 0
            th = d + FRAME_MS if d else 0
            return (f"queue name={name} max-size-time={(th + 500) * 1000000} max-size-buffers={0 if d else 30} "
                    f"leaky=downstream" + (f" min-threshold-time={th * 1000000}" if d else ""))
        plan = (self.always_plan(c) or self.swap_plan(c)) if pip else None
        if plan:
            return self._build_dual(c, plan, base, small, xy, pip2, pip3, sty)
        out.append(f"rtmpsrc location={base}/{c['main']} do-timestamp=true !\nflvdemux name=demux\n")
        if pip:
            # Kein videorate/textoverlay: sie halten das Hauptbild fest, dann wäre das Hineinschreiben
            # nicht mehr in-place (gemessen: bremst). Die Kameras liefern ohnehin 30 fps.
            v = (f"demux.video !\n{qv} !\nidentity name=v_delay signal-handoffs=TRUE ! h264parse ! mppvideodec !\n"
                 "video/x-raw,format=NV12 !\n"
                 f"pbpipmix name=pipmix corner={c['corner']}{xy(c, 'x', 'y', c['corner'])} width-pct={c['size_pct']}{sty(0, 1)}"
                 + (f" slot2=1 corner2={c['corner2']}{xy(c, 'x2', 'y2', c['corner2'])}{sty(1, 2)}" if pip2 and multi else "")
                 + (f" slot3=2 corner3={c['corner3']}{xy(c, 'x3', 'y3', c['corner3'])}{sty(2, 3)}" if pip3 else "") + " !\n"
                 + (f"pbpipmix name=pipmix2 slot=1 corner={c['corner2']}{xy(c, 'x', 'y', c['corner2'])} width-pct={c['size_pct']}{sty(1, 1)} !\n" if pip2 and not multi else "") +
                 "queue max-size-time=500000000 max-size-buffers=4 leaky=downstream !\n")
        else:
            v = (f"demux.video !\n{q} !\nidentity name=v_delay signal-handoffs=TRUE ! h264parse ! mppvideodec !\n"
                 "videorate ! video/x-raw,framerate=30/1,format=NV12 ! queue !\n")
        v += ("mpph265enc zero-copy-pkt=0 qp-max=51 gop=60 name=venc_bps !\n"
              f"h265parse config-interval=-1 ! {q} ! mux.\n")
        out.append(v)
        vol = "volume name=avol ! " if pip and plugin_view() else ""             # Stummschalten im Betrieb (pbctl, siehe gst/gstpbpip.c)
        opus = f"audioconvert ! audioresample quality=10 sinc-filter-mode=1 ! {vol}opusenc bitrate=128000 ! opusparse"
        audio_sel = c.get("audio", "main") if pip else "main"
        if audio_sel == "pip2" and not pip2 or audio_sel == "pip3" and not pip3 or audio_sel not in ("main", "pip", "pip2", "pip3"):
            audio_sel = "main"
        main_audio = audio_sel == "main"
        mute = "queue max-size-buffers=4 leaky=downstream ! fakesink sync=false async=false\n"
        def small_audio(demux, sel):
            if audio_sel == sel:
                return (f"{demux}.audio !\n{q} !\naacparse ! avdec_aac ! identity name=a_delay signal-handoffs=TRUE !\n"
                        f"{opus} ! {q} ! mux.\n")
            return f"{demux}.audio !\n{mute}"
        if main_audio:
            out.append(f"demux.audio !\n{qa} !\naacparse ! avdec_aac ! identity name=a_delay signal-handoffs=TRUE !\n"
                       f"{opus} ! {q} ! mux.\n")
        else:
            out.append("demux.audio !\nqueue max-size-buffers=4 leaky=downstream ! fakesink sync=false async=false\n")
        if pip:
            # Jedes kleine Bild hat seine eigene Größe (Prozent der Bildbreite); der Hardware-Decoder verkleinert auf genau diese Größe
            (w, h), (w2, h2), (w3, h3) = pip_size(c["size_pct"]), pip_size(c["size_pct2"]), pip_size(c["size_pct3"])
            out.append(f"rtmpsrc location={base}/{c['pip']} do-timestamp=true !\nflvdemux name=pdemux\n")
            out.append(f"pdemux.video !\n{small('pipq_v', 'pip_delay_ms')} !\n"
                       f"h264parse ! {small_decode(w, h)}"
                       "queue max-size-time=300000000 max-size-buffers=2 leaky=downstream ! pbpipsink\n")
            if pip2:
                out.append(f"rtmpsrc location={base}/{c['pip2']} do-timestamp=true !\nflvdemux name=p2demux\n")
                out.append(f"p2demux.video !\n{small('pip2q_v', 'pip2_delay_ms')} !\n"
                           f"h264parse ! {small_decode(w2, h2)}"
                           "queue max-size-time=300000000 max-size-buffers=2 leaky=downstream ! pbpipsink slot=1\n")
                out.append(small_audio("p2demux", "pip2"))
            if pip3:
                out.append(f"rtmpsrc location={base}/{c['pip3']} do-timestamp=true !\nflvdemux name=p3demux\n")
                out.append(f"p3demux.video !\n{small('pip3q_v', 'pip3_delay_ms')} !\n"
                           f"h264parse ! {small_decode(w3, h3)}"
                           "queue max-size-time=300000000 max-size-buffers=2 leaky=downstream ! pbpipsink slot=2\n")
                out.append(small_audio("p3demux", "pip3"))
            out.append(small_audio("pdemux", "pip"))
        if pip:
            # Steuerung: liest die Verzögerung aus /var/lib/pipbox/main-delay-ms und stellt die beiden Queues im Betrieb um
            out.append("pbctl name=pbctl video-queue=mainq_v audio-queue=mainq_a pip-queue=pipq_v"
                       + (" pip2-queue=pip2q_v" if pip2 else "") + (" pip3-queue=pip3q_v" if pip3 else "") + "\n")
        out.append("mpegtsmux name=mux !\nappsink name=appsink\n")
        return "\n".join(out)

    @staticmethod
    def _style_props(c):
        """Funktion (Stelle 0 bis 2, Nummer der Eigenschaft) -> Text " styleN=\"...\"" für pbpipmix, leer bei Standardaussehen oder wenn der
        installierte Baustein die Eigenschaft nicht kennt."""
        texts = [style_text(c["styles"][k]) for k in STYLE_SLOTS] if plugin_style() else ["", "", ""]

        def sty(pos, n):
            return f' style{n}="{texts[pos]}"' if texts[pos] else ""
        return sty

    def _build_dual(self, c, plan, base, small, xy, pip2, pip3, sty):
        """Pipeline für den Tausch ohne Unterbrechung (siehe swap_plan). Jede Kamera der Gruppe liefert ihr Bild zweimal: groß in den
        Umschalter vsel (Hauptbild), klein in einen eigenen Platz des Bild-in-Bild (Platz = Kamera + 3 mod 4: Kamera 0 -> 3, 1 -> 0 ...).
        Welche Kamera gerade Hauptbild ist und wo die anderen kleiner erscheinen, schreibt vsel in jedes Bild; pbpipmix liest es dort.
        Der Ton läuft bei Bedarf über einen zweiten Umschalter asel, den pbctl im selben Schritt mitstellt. Die Wartezeit gehört je
        Kamera zu allen ihren Warteschlangen (vfq = Bild groß, vsq = Bild klein, aq = Ton)."""
        q = self.Q
        cams, group, ap = plan["cams"], plan["group"], plan["audio_pos"]
        always = bool(plan.get("always"))          # Eingänge von den Zubringern (udpsrc, enden nie) statt rtmpsrc ! flvdemux
        # Größe des kleinen Bildes je Kamera: die der Stelle, an der sie beim Aufbau steht (Stelle 1 bis 3); die Hauptkamera bekommt die der Stelle 1.
        # Beim Tausch folgt die Größe der Kamera, nicht der Stelle (der Decoder verkleinert fest auf diese Größe).
        sizes = [pip_size(c["size_pct"]), pip_size(c["size_pct2"]), pip_size(c["size_pct3"])]
        out = []
        fillv = f' fill-caps="{FEED_FILL_VIDEO}"' if always else ""
        filla = f' fill-caps="{FEED_AUDIO_CAPS}"' if always else ""
        out.append(f"pbpipsel name=vsel tag-offset=true force-key=true state={plan['state']}{fillv} !\n"
                   "identity name=v_delay signal-handoffs=TRUE !\nvideo/x-raw,format=NV12 !\n"
                   f"pbpipmix name=pipmix follow-tag=true corner={c['corner']}{xy(c, 'x', 'y', c['corner'])} width-pct={c['size_pct']}{sty(0, 1)}"
                   + (f" slot2=1 corner2={c['corner2']}{xy(c, 'x2', 'y2', c['corner2'])}{sty(1, 2)}" if pip2 else "")
                   + (f" slot3=2 corner3={c['corner3']}{xy(c, 'x3', 'y3', c['corner3'])}{sty(2, 3)}" if pip3 else "") + " !\n"
                   "queue max-size-time=500000000 max-size-buffers=4 leaky=downstream !\n"
                   "mpph265enc zero-copy-pkt=0 qp-max=51 gop=60 name=venc_bps !\n"
                   f"h265parse config-interval=-1 ! {q} ! mux.\n")
        vol = "volume name=avol ! " if plugin_view() else ""                      # Stummschalten im Betrieb (pbctl, siehe gst/gstpbpip.c)
        opus = f"audioconvert ! audioresample quality=10 sinc-filter-mode=1 ! {vol}opusenc bitrate=128000 ! opusparse"
        mute = "queue max-size-buffers=4 leaky=downstream ! fakesink sync=false async=false\n"
        queues = []
        for i, key in enumerate(cams):
            d = int(c.get(DELAY_KEYS[i], 0) or 0)
            items = [f"vsq{i}:s"]
            vport, aport = FEED_PORT + 2 * i, FEED_PORT + 2 * i + 1
            if always:
                vsrc = (f'udpsrc port={vport} address=127.0.0.1 buffer-size=8388608 do-timestamp=true caps="{FEED_VIDEO_CAPS}" !\n'
                        "rtph264depay ! h264parse config-interval=-1 !\n")
            else:
                out.append(f"rtmpsrc location={base}/{key} do-timestamp=true !\nflvdemux name=dm{i}\n")
                vsrc = f"dm{i}.video !\n"
            w, h = sizes[max(i - 1, 0)]
            small_chain = (f"{small(f'vsq{i}', DELAY_KEYS[i])} !\nh264parse ! {small_decode(w, h)}"
                           f"queue max-size-time=300000000 max-size-buffers=2 leaky=downstream ! pbpipsink slot={(i + 3) % 4}\n")
            if i < group:
                qv = f"{q} name=vfq{i}" + (f" min-threshold-time={(d + FRAME_MS) * 1000000}" if d else "")
                out.append(f"{vsrc}tee name=vt{i}\n")
                out.append(f"vt{i}. !\n{qv} !\nh264parse ! mppvideodec !\nvideo/x-raw,format=NV12 !\nvsel.sink_{i}\n")
                out.append(f"vt{i}. !\n{small_chain}")
                items.insert(0, f"vfq{i}:v")
            else:
                out.append(f"{vsrc}{small_chain}")
            qa = f"{q} name=aq{i}" + (f" min-threshold-time={d * 1000000}" if d else "")
            if plan["asel"] and i < group:
                if always:                                           # der Zubringer liefert den Ton schon als PCM (48 kHz, Stereo)
                    out.append(f'udpsrc port={aport} address=127.0.0.1 buffer-size=1048576 do-timestamp=true caps="{FEED_AUDIO_CAPS}" !\n{qa} !\nasel.sink_{i}\n')
                else:
                    out.append(f"dm{i}.audio !\n{qa} !\naacparse ! avdec_aac ! audioconvert ! audioresample quality=10 sinc-filter-mode=1 !\n"
                               f"audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved ! asel.sink_{i}\n")
                items.append(f"aq{i}:a")
            elif not plan["asel"] and i == ap + 1:
                out.append(f"dm{i}.audio !\n{qa} !\naacparse ! avdec_aac ! identity name=a_delay signal-handoffs=TRUE !\n{opus} ! {q} ! mux.\n")
                items.append(f"aq{i}:a")
            else:
                out.append(f"dm{i}.audio !\n{mute}")
            queues.append(f" cam{i}={','.join(items)}")
        if plan["asel"]:
            out.append(f"pbpipsel name=asel state={0 if ap < 0 else ap + 1}{filla} !\nidentity name=a_delay signal-handoffs=TRUE !\n"
                       f"{vol}opusenc bitrate=128000 ! opusparse ! {q} ! mux.\n")
        out.append("pbctl name=pbctl selector=vsel" + (" audio-selector=asel" if plan["asel"] else "") + f" audio-pos={ap}" + "".join(queues)
                   + (f" live-file={CAM_LIVE}" if always else "") + "\n")
        out.append("mpegtsmux name=mux !\nappsink name=appsink\n")
        return "\n".join(out)

    def status(self, cams):
        keys = [c["key"] for c in cams]
        return {"config": dict(self.cfg, styles=clean_styles(self.cfg.get("styles"))), "plugin_style": plugin_style(), "cameras": [{"key": c["key"], "name": c["name"], "state": c.get("state", "unknown"), "w": c.get("w", 0), "h": c.get("h", 0)} for c in cams],
                "corners": list(pip_corners()), "preview": self.build() if self.cfg.get("main") in keys else "",
                "needs_plugin": self.cfg.get("type") == "pip", "plugin_present": os.path.exists("/opt/pipbox/gst/libgstpbpip.so")}


def view_only_change(a, b):
    """Unterscheiden sich zwei Bildaufbau-Einstellungen nur in "Bild einblenden" (je Stelle) und der Tonquelle? Nur das lässt sich in der laufenden
    Sendekette ohne Neustart umschalten."""
    def norm(c):
        c = dict(c)
        c["audio"] = "main"
        c["styles"] = clean_styles(c.get("styles"))
        for k in STYLE_SLOTS:
            c["styles"][k] = dict(c["styles"][k], visible=True)
        return c
    return a.get("type") == "pip" and norm(a) == norm(b)


def always_compatible(a, b):
    """Unterscheiden sich zwei Bildaufbau-Einstellungen nur in Dingen, die die Sendekette im Modus "alle Kameras immer bereit" ohne Neustart übernimmt?
    Das sind: welche Kamera an welchem Platz ist (die Zahl der kleinen Bilder muss gleich bleiben), Verzögerungen, Tonquelle, "Bild einblenden",
    deaktivierte Kameras, automatisches Umschalten. Alles andere (Art, Ecken, Größen, Stile, Schalter selbst) ändert den Aufbau."""
    def norm(c):
        c = dict(c)
        for k in ("main", "pip", "pip2", "pip3"):
            c[k] = bool(c.get(k))
        for k in DELAY_KEYS:
            c.pop(k, None)
        for k in ("audio", "inactive", "auto_failover", "swap_cams"):
            c.pop(k, None)
        c["styles"] = clean_styles(c.get("styles"))
        for k in STYLE_SLOTS:
            c["styles"][k] = dict(c["styles"][k], visible=True)
        return c
    if pipbox_live.supported() and a.get("type") == "pip" and b.get("type") == "pip" and a.get("always_ready") is True and b.get("always_ready") is True:
        return True                       # Engine Compositor: Zuordnung, Größen, Ecken, Stile, Zuschnitt, Rahmen, Verzögerung, Ton: alles live
    return a.get("type") == "pip" and b.get("type") == "pip" and a.get("always_ready") is True and b.get("always_ready") is True and norm(a) == norm(b)


class SendControl:
    """Live gehen / beenden. Die eigentliche Sendekette läuft als Root-Dienst (pipbox-send);
    wir stellen nur Zustand und Voraussetzungen dar und legen eine Anforderungsdatei ab."""
    STATUS = "/run/pipbox-send/status.json"
    SWAP_WAIT = 2.0               # so lange auf die Rückmeldung des Bausteins warten (Sekunden)

    def __init__(self, state_dir, srtla, pipeline, cams, demo=False):
        self.req = os.path.join(state_dir, "send-request")
        self.srtla, self.pipeline, self.cams, self.demo = srtla, pipeline, cams, demo

    def _active(self):
        if self.demo:
            return False
        hit = getattr(self, "_act", None)
        if hit is not None and time.monotonic() - hit[0] < 1.5:      # mehrere Abfragen pro Takt teilen sich ein systemctl
            return hit[1]
        try:
            r = subprocess.run(["systemctl", "is-active", "pipbox-send.service"],
                               capture_output=True, text=True, timeout=4)
            val = r.stdout.strip() in ("active", "activating", "deactivating")
        except (OSError, subprocess.TimeoutExpired):
            # systemctl antwortet unter Last manchmal nicht in 4 s: das ist kein "Sendung aus". Der Sende-Dienst schreibt seinen Zustand alle 2 s.
            try:
                with open(self.STATUS) as f:
                    st = json.load(f)
                val = time.time() - float(st.get("time", 0)) < 15 and st.get("state") not in ("stopping", "refused")
            except (OSError, ValueError, TypeError):
                val = False
        self._act = (time.monotonic(), val)
        return val

    def _detail(self):
        try:
            with open(self.STATUS) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def picture(self):
        """Welche eingestellte Kamera ist gerade im Bild? {Schlüssel: ("an"|"wartet"|"aus", Sekunden bis zur Aufnahme)}
        oder None, wenn nicht gesendet wird oder keine Automatik läuft."""
        if not self._active():
            return None
        fo = self._detail().get("failover")
        if not fo:
            return None
        layout, wait = set(fo.get("layout") or []), fo.get("wait") or {}
        out = {}
        for k in fo.get("configured") or []:
            if k in layout:
                out[k] = ("an", 0)
            elif k in wait:
                out[k] = ("wartet", int(wait[k]))
            else:
                out[k] = ("aus", 0)
        return out

    def reasons(self):
        r = []
        pub = self.srtla.public()
        if not pub["selected"]:
            r.append("Kein SRTLA-Server ausgewählt")
        cfg = self.pipeline.cfg
        if not cfg.get("main"):
            r.append("Kein Bildaufbau gespeichert")
        else:
            live = {c["key"]: c.get("state") for c in self.cams.listing("")}
            keys = [k for k in (cfg["main"], cfg.get("pip", ""), cfg.get("pip2", ""), cfg.get("pip3", "")) if k]
            if PipelineStore.always_plan(cfg) and PipelineStore.always_plan(cfg).get("live"):
                pass                                  # Engine "immer bereit": die Sendung startet auch ohne Kamera, jede Kamera kommt dazu, sobald sie sendet
            elif cfg.get("auto_failover", True) and len(set(keys)) > 1:
                # Automatisch umschalten: es genügt, wenn mindestens eine Kamera sendet (eine deaktivierte nur, wenn sie das Hauptbild ist)
                off = set(cfg.get("inactive") or [])
                if not any(live.get(k) == "live" for k in keys if k == cfg["main"] or k not in off):
                    r.append("Es sendet nur eine deaktivierte Kamera" if any(live.get(k) == "live" for k in keys) else "Keine Kamera sendet gerade")
            else:
                for k in sorted(set(keys)):
                    if live.get(k) != "live":
                        r.append(f"Kamera {k} sendet gerade nicht")
        if cfg.get("type") == "pip" and not os.path.exists("/opt/pipbox/gst/libgstpbpip.so"):
            r.append("Bild-in-Bild: der Überlagerungs-Baustein ist noch nicht installiert")
        return r

    def status(self):
        active = self._active()
        d = self._detail()
        pub = self.srtla.public()
        sel = next((s for s in pub["servers"] if s["id"] == pub["selected"]), None)
        state = d.get("state") if active else ("refused" if d.get("state") == "refused" else "stopped")
        out = {"active": active, "state": state or "stopped", "message": d.get("message", ""), "last": d.get("last", ""), "last_age": d.get("last_age"),
               "since": d.get("since"), "restarts": d.get("restarts"), "delay_live": bool(d.get("delay_live")), "delay_live_pips": bool(d.get("delay_live_pips")),
               "server": sel and {"name": sel["name"], "host": sel["host"], "port": sel["port"]}}
        out["failover"] = d.get("failover") if active else None
        out["view_live"], out["audio_live"], out["view"] = self.view_live(), self.audio_live(), self.view_state()
        out["footer"] = self.footer()
        sw = d.get("swap") if active else None
        out["swap_group"] = [k for k in sw["cams"][:int(sw.get("group", 0))] if isinstance(k, str)] if isinstance(sw, dict) and isinstance(sw.get("cams"), list) else []
        applied = d.get("applied") if active else None
        cur = srtla_signature(self.srtla.data)
        out["pending"] = [lab for k, lab in PENDING_LABELS if isinstance(applied, dict) and k in applied and applied[k] != cur[k]]
        out["reasons"] = [] if active else self.reasons()
        if not active and belacoder_running():
            out["reasons"].append("Es läuft schon ein belacoder (z. B. über die BELABOX-Oberfläche)")
        out["can_start"] = not active and not out["reasons"]
        return out

    def request(self, action, confirm):
        if action == "start":
            if not confirm:
                raise ValueError("Bestätigung fehlt")
            st = self.status()
            if st["active"]:
                raise ValueError("Es wird schon gesendet")
            if not st["can_start"]:
                raise ValueError("Senden nicht möglich: " + "; ".join(st["reasons"]))
        elif action == "restart":
            why = [] if self.demo else self.reasons()
            if why:
                raise ValueError("Neustart nicht möglich: " + "; ".join(why))
        elif action != "stop":
            raise ValueError("Unbekannte Aktion")
        if action == "start" and not self.demo:
            self.reset_mute()                      # eine neue Sendung beginnt nie stumm
        self._act = None
        if self.demo:
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n")


    def delay_live(self):
        """Hat die laufende Sendekette den Steuerbaustein (Verzögerung des Hauptbildes ohne Neustart änderbar)?"""
        d = self._detail()
        # Im Notbetrieb (Anordnung weicht von der Einstellung ab) passen die Regler nicht zu den Plätzen: neu starten
        return bool(self._active() and d.get("delay_live") and not (d.get("failover") or {}).get("degraded"))

    def delay_live_pips(self):
        """Dasselbe für die Verzögerung der kleinen Bilder (neuere Version des Steuerbausteins)."""
        d = self._detail()
        return bool(self._active() and d.get("delay_live_pips") and not (d.get("failover") or {}).get("degraded"))

    def swap_live(self):
        """Tausch des Hauptbilds in der laufenden Sendekette übernehmen lassen, ohne Neustart (nur im Tausch-Betrieb und wenn die
        Anordnung der Kameras noch zum Aufbau passt). Wahr, wenn der Baustein den neuen Zustand zurückgemeldet hat; sonst bleibt der Neustart."""
        if self.demo or not self._active():
            return False
        d = self._detail()
        sw = d.get("swap")
        if not isinstance(sw, dict) or not isinstance(sw.get("cams"), list) or (d.get("failover") or {}).get("degraded"):
            return False
        cfg = self.pipeline.cfg
        slots = [cfg.get(k) for k in ("main", "pip", "pip2", "pip3")]
        try:
            line = PipelineStore.swap_line(slots, sw["cams"], int(sw.get("group", 0)))
            if line is None:
                return False
            live = {c["key"]: c.get("state") for c in self.cams.listing("")}
            if live.get(cfg.get("main")) != "live":          # die neue Hauptkamera sendet nicht: lieber neu starten (Notbetrieb)
                return False
        except (TypeError, ValueError, KeyError):
            return False
        folder = os.path.dirname(self.req)
        tmp = os.path.join(folder, SWAP_SELECT + ".tmp")
        t0 = time.time()
        try:
            with open(tmp, "w") as f:
                f.write(line + "\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(folder, SWAP_SELECT))
        except OSError:
            return False
        end = time.monotonic() + self.SWAP_WAIT
        while time.monotonic() < end:                       # der Baustein liest alle 0,1 s und meldet den Zustand zurück
            try:
                if os.stat(SWAP_STATE).st_mtime >= t0 - 0.2:
                    with open(SWAP_STATE) as f:
                        if f.read().split() == line.split():
                            return True
            except OSError:
                pass
            time.sleep(0.1)
        return False

    def reset_mute(self):
        """Stumm gilt nur für die laufende Sendung: vor dem Start die Ansicht-Datei ohne Stumm neu schreiben (der Sender liest sie beim Aufbau)."""
        folder = os.path.dirname(self.req)
        try:
            tmp = os.path.join(folder, VIEW_FILE + ".tmp")
            with open(tmp, "w") as f:
                f.write(PipelineStore.view_line(self.pipeline.cfg, 0) + "\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(folder, VIEW_FILE))
        except OSError:
            pass

    def view_live(self):
        """Läuft die Sendekette mit der Ansicht im Betrieb (kleine Bilder ein-/ausblenden und stumm ohne Neustart)? Im Notbetrieb (Anordnung weicht
        von der Einstellung ab) passen die Stellen nicht zu den Kameras: dann nicht."""
        if self.demo:
            return True
        d = self._detail()
        return bool(self._active() and d.get("view_live") and not (d.get("failover") or {}).get("degraded"))

    def always_live(self):
        """Läuft die Sendekette im Modus "alle Kameras immer bereit" (Kamerawechsel und Rückkehr ohne Neustart)?"""
        if self.demo:
            return False
        return bool(self._active() and self._detail().get("always"))

    def view_degraded(self):
        """Läuft die Sendung gerade im Notbetrieb (weniger Kameras als eingestellt)?"""
        if self.demo:
            return False
        return bool(self._active() and (self._detail().get("failover") or {}).get("degraded"))

    def audio_live(self):
        """Dasselbe für die Tonquelle (braucht den Ton-Umschalter der laufenden Sendekette)."""
        return True if self.demo else bool(self.view_live() and self._detail().get("audio_live"))

    def view_state(self):
        """Was der Baustein gerade eingestellt hat: {"hide": Bits, "audio": -1..2, "mute": bool} oder None."""
        if self.demo:
            return getattr(self, "_fake_view", None)
        if not self._active():
            return None
        try:
            hide, audio, mute = (int(x) for x in open(VIEW_STATE).read().split()[:3])
        except (OSError, ValueError):
            return None
        return {"hide": hide, "audio": audio, "mute": bool(mute)}

    def footer(self):
        """Angaben für die Fußleiste am Handy (Issue #19): die Kameras im Bild in der Reihenfolge der Kameraliste (so springen die Knöpfe nach einem
        Tausch nicht), welche Hauptbild und welche ausgeblendet ist, und die Tonquelle samt Stumm. Während der Sendung gilt, was der Baustein
        meldet, sonst die Einstellung. None, wenn die Art nicht Bild-in-Bild ist."""
        c = PipelineStore._safe_cfg(self.pipeline.cfg)
        pip, pip2, pip3, _ = PipelineStore._layout(c)
        if c["type"] != "pip" or not c.get("main") or not pip:
            return None
        order = ("main", "pip", "pip2", "pip3")
        place = {"main": c["main"], "pip": c["pip"], "pip2": c["pip2"] if pip2 else "", "pip3": c["pip3"] if pip3 else ""}
        st = self.view_state() if self.view_live() else None
        if st:
            hide, apos, mute = st["hide"], st["audio"], bool(st["mute"])
        else:
            (hide, apos), mute = PipelineStore.view_values(c), False
        src = {v: k for k, v in PipelineStore.AUDIO_POS.items()}.get(apos, "main")
        if not place.get(src):
            src = "main"
        slot_of = {}
        for i, k in enumerate(order):
            if place[k]:
                slot_of.setdefault(place[k], i)
        listed = {x["key"]: x for x in self.cams.listing("")}
        keys = [k for k in listed if k in slot_of] + [k for k in slot_of if k not in listed]
        cams = [{"key": k, "name": (listed.get(k) or {}).get("name") or k, "state": (listed.get(k) or {}).get("state", "unknown"),
                 "slot": slot_of[k], "main": slot_of[k] == 0, "hidden": slot_of[k] > 0 and bool(hide >> (slot_of[k] - 1) & 1),
                 "inactive": slot_of[k] > 0 and k in (c.get("inactive") or [])} for k in keys]
        off = set(c.get("inactive") or [])
        options = [k for k in order if place[k] and (k == "main" or place[k] not in off)]      # deaktivierte Kameras sind keine Tonquelle (ausgeblendete schon)
        starting = self._detail().get("state") in ("starting", "restarting")      # noch keine Meldung der Kameras: alle Knöpfe lassen (die Leiste springt sonst beim Start)
        if self.always_live() and ((self._detail().get("failover") or {}).get("live") or not starting):
            # "alle Kameras immer bereit": Knöpfe und Tonwahl nur für Kameras, deren Bilder gerade ankommen; das Hauptbild ist, wer es jetzt wirklich ist
            fo = self._detail().get("failover") or {}
            live = set(fo.get("live") or [])
            effmain = fo.get("main")
            cams = [dict(x, main=(x["key"] == effmain) if effmain else x["main"]) for x in cams if x["key"] in live or x["key"] == effmain]
            options = [k for k in options if place[k] in live or place[k] == effmain]
            if place.get(src) not in live and options:
                src = options[0]
            if src not in options:
                options = [src]
        aud_key = place[src]
        nxt = options[(options.index(src) + 1) % len(options)] if src in options else (options[0] if options else "main")
        return {"cams": cams,
                "audio": {"src": src, "key": aud_key, "name": (listed.get(aud_key) or {}).get("name") or aud_key, "mute": mute,
                          "next": nxt}}

    def apply_view(self, hide, audio, mute):
        """Ansicht in die laufende Sendekette übernehmen, ohne Neustart. Wahr, wenn der Baustein den neuen Zustand zurückgemeldet hat (so, wie er
        wirklich gilt); sonst bleibt der Neustart. Die Zeile schreibt dieser Dienst, den Zustand meldet pbctl zurück."""
        line = f"{int(hide)} {int(audio)} {1 if mute else 0}"
        if self.demo:
            self._fake_view = {"hide": int(hide), "audio": int(audio), "mute": bool(mute)}
            return True
        if not self._active():
            return False
        folder = os.path.dirname(self.req)
        tmp = os.path.join(folder, VIEW_FILE + ".tmp")
        t0 = time.time()
        try:
            with open(tmp, "w") as f:
                f.write(line + "\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(folder, VIEW_FILE))
        except OSError:
            return False
        end = time.monotonic() + self.SWAP_WAIT
        while time.monotonic() < end:                       # der Baustein liest alle 0,1 s und meldet den Zustand zurück
            try:
                if os.stat(VIEW_STATE).st_mtime >= t0 - 0.2:
                    with open(VIEW_STATE) as f:
                        if f.read().split() == line.split():
                            print(f"Ansicht live umgestellt ({line}), bestätigt nach {time.time() - t0:.2f} s", flush=True)    # im Journal: Zeit des Wechsels
                            return True
            except OSError:
                pass
            time.sleep(0.1)
        print(f"Ansicht ({line}) nicht bestätigt, Neustart der Sendung folgt", flush=True)
        return False

    def change_view(self, visible=None, audio=None, mute=None):
        """Sichtbarkeit, Tonquelle und Stumm ändern. Speichert Sichtbarkeit und Tonquelle in der Einstellung, schaltet in der laufenden Sendung ohne
        Neustart um und startet nur dann neu, wenn der Baustein das nicht kann. Stumm gilt nur für die laufende Sendung (ohne Sendung nichts zu tun).
        Gibt {"live": ob ohne Neustart, "restarted": ob neu gestartet, "note": Text, "view": Zustand} zurück."""
        if mute is not None and not isinstance(mute, bool):
            raise ValueError("Stumm: ja oder nein")
        if visible is None and audio is None and mute is None:
            raise ValueError("Nichts zu ändern")
        active = self._active() if not self.demo else True
        if mute is not None and not active:
            raise ValueError("Stumm schalten geht nur während der Sendung")
        before = self.view_state()
        old_hide, old_audio = PipelineStore.view_values(self.pipeline.cfg)
        hide, aud = self.pipeline.set_view(visible, audio) if (visible is not None or audio is not None) else (old_hide, old_audio)
        cur_mute = bool(before["mute"]) if before else False
        new_mute = cur_mute if mute is None else mute
        if not active:
            return {"live": False, "restarted": False, "note": "Gespeichert. Gilt ab dem nächsten Start der Sendung.", "view": None}
        needs_audio = audio is not None and aud != (before["audio"] if before else old_audio)
        if self.view_live() and (not needs_audio or self.audio_live() or self.demo):
            if self.apply_view(hide, aud, new_mute):
                return {"live": True, "restarted": False, "note": "", "view": self.view_state()}
        if self.view_degraded():
            # Notbetrieb (weniger Kameras als eingestellt): live geht nichts um, und ein Neustart bräche die Sendung ab. Gespeichert gilt es,
            # sobald die Kameras zurück sind und die Sendung mit dem vollen Bild läuft.
            print("Ansicht im Notbetrieb gespeichert, kein Neustart", flush=True)
            if mute is not None and visible is None and audio is None:
                raise ValueError("Stumm schalten geht im Notbetrieb nicht. Es gilt wieder, sobald alle Kameras da sind.")
            return {"live": False, "restarted": False, "note": "Gespeichert. Gilt, sobald alle Kameras wieder da sind (Notbetrieb: ohne Neustart).", "view": None}
        if mute is not None and visible is None and audio is None:
            raise ValueError("Stumm schalten ging gerade nicht (die Sendekette kennt das noch nicht). Bitte die Sendung einmal neu starten.")
        restarted, note = self.restart_if_live()
        return {"live": False, "restarted": restarted, "note": note, "view": None}

    def restart_if_live(self):
        """Nach geänderter Pipeline: läuft die Sendekette, wird sie kurz neu gestartet.
        Vorher wird geprüft, ob die neue Pipeline überhaupt starten kann; sonst läuft die alte weiter.
        Gibt (neu gestartet?, Hinweis) zurück."""
        if not self._active():
            return False, ""
        why = self.reasons()
        if why:
            return False, "Gespeichert, aber die laufende Übertragung wurde nicht neu gestartet: " + "; ".join(why)
        self.request("restart", True)
        return True, "Gespeichert. Die Übertragung wird jetzt kurz neu gestartet."


KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ROLES = ("main", "pip", "extra")


class CameraStore:
    """RTMP-Kameras: Name, Rolle und Stream-Schlüssel in einer JSON-Datei.

    Die Kamera sendet an rtmp://<Box>:1935/<app>/<Schlüssel>. Ob sie gerade
    sendet, kommt aus der nginx-rtmp-Statistik (--rtmp-stat-url); ohne diese
    Adresse ist der Status "unbekannt".
    """

    def __init__(self, path, app, stat_url, demo):
        self.path, self.app, self.stat_url, self.demo = path, app, stat_url, demo
        self.lock = threading.Lock()
        self.cams = []
        self.ipfn = lan_ip
        self.ifaces = iface_ips     # in Tests ersetzbar
        self.forgotten = set()      # Schlüssel entfernter Kameras: ein noch sendender Stream wird nicht von selbst wieder aufgenommen
        try:
            with open(path) as f:
                self.cams = json.load(f)
        except (OSError, ValueError):
            pass
        try:
            with open(path + ".removed") as f:
                self.forgotten = {k for k in json.load(f) if isinstance(k, str)}
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.cams, f, indent=1)
        os.replace(tmp, self.path)

    def _save_forgotten(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".removed.tmp"
        with open(tmp, "w") as f:
            json.dump(sorted(self.forgotten)[-200:], f)
        os.replace(tmp, self.path + ".removed")

    def forget(self, key):
        """Nach "Kamera entfernen": ein Stream mit diesem Schlüssel kommt nicht von selbst wieder in die Liste (Hinzufügen von Hand oder eine neu eingerichtete DJI-Kamera heben das auf)."""
        if key and key not in self.forgotten:
            self.forgotten.add(key)
            self._save_forgotten()

    def add(self, name, key, role):
        name = (name or "").strip()[:40]
        if not name:
            raise ValueError("Name fehlt")
        if role not in ROLES:
            raise ValueError("Rolle ungültig")
        key = (key or "").strip().lower() or "cam-" + secrets.token_hex(3)
        if not KEY_RE.match(key):
            raise ValueError("Schlüssel: nur a-z, 0-9, - und _, höchstens 32 Zeichen")
        with self.lock:
            if any(c["key"] == key for c in self.cams):
                raise ValueError("Schlüssel existiert schon")
            if key in self.forgotten:
                self.forgotten.discard(key)
                self._save_forgotten()
            if role in ("main", "pip") and any(c["role"] == role for c in self.cams):
                raise ValueError("Diese Rolle ist schon vergeben")
            cam = {"id": secrets.token_hex(4), "name": name, "key": key, "role": role}
            self.cams.append(cam)
            self.save()
        return cam

    def remove(self, cam_id):
        with self.lock:
            n = len(self.cams)
            self.cams = [c for c in self.cams if c["id"] != cam_id]
            if len(self.cams) != n:
                self.save()
            return len(self.cams) != n

    def auto_add(self):
        """Unbekannte, sendende Streams automatisch als Kamera aufnehmen."""
        live = self.live_streams() or {}
        added = []
        with self.lock:
            known = {c["key"] for c in self.cams}
            for key in sorted(live):
                if key in known or key in self.forgotten or not KEY_RE.match(key) or key.startswith("test-"):
                    continue   # "test-…" sind Testquellen und gehören nicht in die Kameraliste
                roles = {c["role"] for c in self.cams}
                role = "main" if "main" not in roles else "pip" if "pip" not in roles else "extra"
                cam = {"id": secrets.token_hex(4), "name": f"Kamera {key}", "key": key,
                       "role": role, "auto": True}
                self.cams.append(cam)
                added.append(cam)
            if added:
                self.save()
        return added

    def host_for_iface(self, name):
        """Adresse der Box in der Verbindung <name> (feste Zweitadresse, sonst die der Schnittstelle) oder None, wenn es sie nicht gibt."""
        for o in self.ifaces():
            if o["iface"] == name:
                return o.get("cam_ip") or o["ip"]
        return None

    def update(self, cam_id, name=None, role=None, iface=None):
        with self.lock:
            cam = next((c for c in self.cams if c["id"] == cam_id), None)
            if not cam:
                raise KeyError(cam_id)
            if iface is not None:
                if not isinstance(iface, str):
                    raise ValueError("Verbindung ungültig")
                if iface and self.host_for_iface(iface) is None:
                    raise ValueError("Unbekannte Verbindung oder keine IPv4-Adresse")
                if iface:
                    cam["iface"] = iface
                else:
                    cam.pop("iface", None)                 # leer: wieder die Hauptverbindung
            if name is not None:
                name = name.strip()[:40]
                if not name:
                    raise ValueError("Name fehlt")
                if any(c["id"] != cam_id and c["name"].casefold() == name.casefold() for c in self.cams):
                    raise ValueError("Diesen Namen hat schon eine andere Kamera")
                cam["name"] = name
            if role is not None:
                if role not in ROLES:
                    raise ValueError("Rolle ungültig")
                if role in ("main", "pip") and any(
                        c["role"] == role and c["id"] != cam_id for c in self.cams):
                    raise ValueError("Diese Rolle ist schon vergeben")
                cam["role"] = role
            self.save()
            return cam

    def swap(self, cam_id, other_id):
        """Zwei Kameras in der Liste die Plätze tauschen (Issue #32): die Reihenfolge der Kamera-Knöpfe in der Fußleiste folgt der Liste.
        Hauptbild und kleine Bilder bleiben, wie sie sind (die Zuordnung der Bilder ist eine eigene Einstellung)."""
        with self.lock:
            i = next((n for n, c in enumerate(self.cams) if c["id"] == cam_id), None)
            j = next((n for n, c in enumerate(self.cams) if c["id"] == other_id), None)
            if i is None or j is None:
                raise KeyError(cam_id if i is None else other_id)
            if i != j:
                self.cams[i], self.cams[j] = self.cams[j], self.cams[i]
                self.save()
            return self.cams[j]

    def live_streams(self):
        """Dict Schlüssel -> {fps, mbit} oder None wenn Statistik nicht lesbar."""
        if self.demo:
            return {c["key"]: {"fps": 30.0, "mbit": round(6 + random.random() * 2, 1)}
                    for i, c in enumerate(self.cams) if i % 3 != 2}
        if not self.stat_url:
            return None
        hit = getattr(self, "_live", None)
        if hit is not None and time.monotonic() - hit[0] < 1.5:      # mehrere Abfragen pro Takt teilen sich eine Statistik
            return hit[1]
        res = self._live_read()
        self._live = (time.monotonic(), res)
        return res

    def _live_read(self):
        try:
            with urllib.request.urlopen(self.stat_url, timeout=2) as r:
                root = ET.fromstring(r.read())
        except (OSError, ET.ParseError):
            return None
        res = {}
        for app in root.iter("application"):
            if app.findtext("name") != self.app:
                continue
            for st in app.iter("stream"):
                if st.find("publishing") is None:
                    continue
                bw = (int(st.findtext("bw_video") or 0) + int(st.findtext("bw_audio") or 0))
                fps = st.findtext("meta/video/frame_rate")
                key = st.findtext("name")
                w, h = st.findtext("meta/video/width"), st.findtext("meta/video/height")
                res[key] = {"fps": float(fps) if fps else None, "mbit": round(bw / 1e6, 1),
                            "w": int(w) if w and w.isdigit() else 0, "h": int(h) if h and h.isdigit() else 0}
        return res

    def listing(self, host, addr_for=None):
        """Kameras mit Adresse und Zustand. Die Adresse gilt für die Verbindung der Kamera: DJI-Kameras nach ihrer Karte
        (addr_for(Schlüssel) -> (Adresse, Verbindung) oder None), andere Kameras nach der gewählten Verbindung (Feld iface),
        sonst nach der Hauptverbindung (Kameras lösen keine .local-Namen auf, darum immer eine IP-Adresse)."""
        main = self.ipfn()
        live = self.live_streams()
        out = []
        for c in self.cams:
            st = None if live is None else live.get(c["key"])
            via, src, host = None, "main", main
            over = addr_for(c["key"]) if addr_for else None
            if c["key"] == HDMI_KEY:
                src = "hdmi"                       # das Bild kommt vom HDMI-Eingang der Box: keine Adresse, keine Verbindung zu wählen
            elif over and over[0]:
                host, via, src = over[0], over[1], "dji"
            elif c.get("iface") and self.host_for_iface(c["iface"]):
                host, via, src = self.host_for_iface(c["iface"]), c["iface"], "own"
            out.append({**c, "via": via, "via_src": src,
                        "url": "" if src == "hdmi" else f"rtmp://{host}:1935/{self.app}/{c['key']}",
                        "state": "unknown" if live is None else
                                 ("live" if st else "offline"),
                        "fps": st and st["fps"], "mbit": st and st["mbit"],
                        "w": (st or {}).get("w", 0), "h": (st or {}).get("h", 0)})
        return out


SESSION_SECONDS = 12 * 3600
REMEMBER_SECONDS = 30 * 24 * 3600     # "Angemeldet bleiben": überlebt Updates und Neustarts
MAX_FAILS, FAIL_WINDOW = 5, 300
MAX_CHECKS = 2            # so viele Passwortprüfungen (je ein node-Prozess mit bcrypt oder PBKDF2) laufen höchstens gleichzeitig


class Auth:
    """Ein Passwort für die Oberfläche.

    Auf einer BELABOX gilt das Passwort der belaUI (bcrypt-Hash in deren config.json, nur gelesen). Hat die BELABOX noch keins
    (frisches Image), wartet die Oberfläche darauf: Es wird zuerst in der BELABOX-Oberfläche festgelegt, hier gibt es dann
    weder einen Setup-Code noch ein zweites Passwort ("belabox-wartet").
    Nur ohne belaUI (Entwicklung, Demo): eigenes Passwort. Erster Start: Der Server schreibt einen Setup-Code in
    <state>/setup-code (Rechte 0600, Besitzer pipbox). Wer den Code kennt, legt im Browser das Passwort fest; danach wird die
    Codedatei gelöscht. Passwort nur als PBKDF2-HMAC-SHA256-Hash. Ein eigenes Passwort aus einer früheren Version bleibt gültig.
    Demo (--demo auf dem eigenen Rechner): Das Passwort ist von Anfang an gesetzt (DEMO_PASSWORD, nur im Speicher), ohne Setup-Code;
    die Anmeldeseite füllt es vor. Nie auf einer Box: dort gilt immer das BELABOX-Passwort.
    "Angemeldet bleiben": Eine solche Sitzung gilt 30 Tage und übersteht Neustarts der Oberfläche (z. B. nach einem Update). Auf der Platte
    liegt nur ein SHA-256 des Sitzungsschlüssels (<state>/sessions.json, Rechte 0600) mit der Kennung des Passworts: Ändert sich das
    Passwort (BELABOX oder eigenes), sind diese Sitzungen ungültig. Ohne Haken bleibt die Sitzung nur im Speicher (12 Stunden).
    """

    BELA_JS = ("const b=require(process.argv[1]);let d='';"
               "process.stdin.on('data',c=>d+=c).on('end',()=>"
               "process.exit(b.compareSync(d,process.argv[2])?0:1))")

    DEMO_PASSWORD = "demo"

    def __init__(self, state_dir, bela_config=None, demo=False):
        self.bela_config = bela_config  # belaUI config.json: BELABOX-Passwort mitbenutzen
        self.demo = bool(demo)
        self.dir = state_dir
        self.path = os.path.join(state_dir, "auth.json")
        self.code_path = os.path.join(state_dir, "setup-code")
        self.sessions = {}
        self.store_path = os.path.join(state_dir, "sessions.json")
        self.fails = {}
        self.lock = threading.Lock()
        self.checks = threading.BoundedSemaphore(MAX_CHECKS)
        os.makedirs(state_dir, exist_ok=True)
        self.cfg = None
        try:
            with open(self.path) as f:
                self.cfg = json.load(f)
        except (OSError, ValueError):
            pass
        self.setup_code = None
        if self.demo and not self.bela_hash():
            salt = secrets.token_bytes(16)       # Vorschau: Passwort von Anfang an gesetzt, nichts auf der Platte
            self.cfg = {"salt": salt.hex(), "hash": self._hash(self.DEMO_PASSWORD, salt).hex()}
            try:
                os.remove(self.code_path)
            except OSError:
                pass
        elif not self.cfg and not self.bela_hash() and not self.bela_pending():
            self.setup_code = secrets.token_urlsafe(6)
            fd = os.open(self.code_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(self.setup_code + "\n")
        else:
            try:
                os.remove(self.code_path)     # ein Code von früher ist nicht mehr nötig
            except OSError:
                pass

    def _cred(self):
        """Kennung des gültigen Passworts: ändert es sich, werden gemerkte Sitzungen ungültig."""
        h = self.bela_hash() or ("demo" if self.demo else (self.cfg or {}).get("hash")) or ""   # Demo: Salt ist bei jedem Start neu
        return hashlib.sha256(("pbcred:" + h).encode()).hexdigest()

    @staticmethod
    def _tok_id(tok):
        return hashlib.sha256(tok.encode()).hexdigest()

    def _remembered(self):
        try:
            with open(self.store_path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    def _save_remembered(self, d):
        tmp = self.store_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self.store_path)

    def bela_hash(self):
        """bcrypt-Hash des BELABOX-Passworts (nur gelesen) oder None."""
        if not self.bela_config:
            return None
        try:
            with open(self.bela_config) as f:
                h = json.load(f).get("password_hash")
        except (OSError, ValueError):
            return None
        return h if isinstance(h, str) and h.startswith("$2") else None

    def bela_ok(self, pw, h):
        """Prüft mit dem Node.js/bcrypt von belaUI; Passwort nur über stdin."""
        mod = os.path.join(os.path.dirname(self.bela_config), "node_modules", "bcrypt")
        try:
            r = subprocess.run(["node", "-e", self.BELA_JS, mod, h],
                               input=pw.encode(), timeout=10,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("BELABOX-Passwortprüfung nicht möglich")
        if r.returncode not in (0, 1):
            raise RuntimeError("BELABOX-Passwortprüfung nicht möglich")
        return r.returncode == 0

    def bela_pending(self):
        """Läuft die Oberfläche auf einer BELABOX, die noch kein Passwort hat? (belaUI ist da, ihre Konfiguration enthält aber keinen Hash)"""
        if not self.bela_config or self.bela_hash():
            return False
        return os.path.isdir(os.path.dirname(os.path.abspath(self.bela_config)))

    @property
    def mode(self):
        if self.bela_hash():
            return "belabox"
        if self.demo and self.cfg:
            return "demo"
        if self.cfg:
            return "own"                      # eigenes Passwort aus einer früheren Version bleibt gültig
        return "belabox-wartet" if self.bela_pending() else "own"

    @property
    def configured(self):
        return bool(self.cfg) or self.mode == "belabox"

    @staticmethod
    def _hash(pw, salt):
        return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 300000, dklen=32)

    def throttled(self, ip):
        now = time.time()
        with self.lock:
            f = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
            self.fails[ip] = f
            return len(f) >= MAX_FAILS

    def fail(self, ip):
        with self.lock:
            self.fails.setdefault(ip, []).append(time.time())

    def reserve(self, ip):
        """Zählt einen Versuch SOFORT, vor der Prüfung (Issue #25): Wurde erst nach der Prüfung gezählt, kamen bei vielen gleichzeitigen Anmeldungen
        alle an der Sperre vorbei. Gibt die Marke des Versuchs zurück oder None, wenn die Sperre schon greift. Ein gelungener oder nicht
        durchgeführter Versuch wird mit unreserve zurückgenommen, ein falsches Passwort bleibt gezählt."""
        now = time.time()
        with self.lock:
            f = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
            if len(f) >= MAX_FAILS:
                self.fails[ip] = f
                return None
            f.append(now)
            self.fails[ip] = f
            return now

    def unreserve(self, ip, mark):
        with self.lock:
            f = self.fails.get(ip, [])
            if mark in f:
                f.remove(mark)

    def check_password(self, ip, verify):
        """Führt verify() (die eigentliche Prüfung) mit der Sperre aus: erst zählen, höchstens MAX_CHECKS Prüfungen gleichzeitig, falsch bleibt
        gezählt. verify gibt wahr/falsch zurück. Sperre und Überlastung melden PermissionError (-> 429), ohne eine Prüfung zu starten."""
        mark = self.reserve(ip)
        if mark is None:
            raise PermissionError("Zu viele Versuche, bitte 5 Minuten warten")
        if not self.checks.acquire(blocking=False):
            self.unreserve(ip, mark)
            raise PermissionError("Die Box prüft gerade andere Anmeldungen, bitte gleich noch einmal versuchen")
        try:
            good = verify()
        except BaseException:
            self.unreserve(ip, mark)            # die Prüfung selbst ging schief (nicht das Passwort): nicht als Fehlversuch zählen
            raise
        finally:
            self.checks.release()
        if good:
            self.unreserve(ip, mark)
        return good

    WAITING_MSG = "Auf der BELABOX ist noch kein Passwort gesetzt. Bitte zuerst in der BELABOX-Oberfläche eines festlegen."

    def set_password(self, code, pw, ip):
        if self.mode == "belabox-wartet":
            raise ValueError(self.WAITING_MSG)
        if self.configured or not self.setup_code:
            raise ValueError("Passwort ist schon gesetzt")
        if not self.check_password(ip, lambda: hmac.compare_digest(code or "", self.setup_code)):
            raise ValueError("Setup-Code falsch")
        if len(pw or "") < 10:
            raise ValueError("Passwort: mindestens 10 Zeichen")
        salt = secrets.token_bytes(16)
        self.cfg = {"salt": salt.hex(), "hash": self._hash(pw, salt).hex()}
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.cfg, f)
        os.replace(tmp, self.path)
        self.setup_code = None
        try:
            os.remove(self.code_path)
        except OSError:
            pass

    def login(self, pw, ip, remember=False):
        if not self.configured:
            raise ValueError(self.WAITING_MSG if self.mode == "belabox-wartet" else "Noch kein Passwort gesetzt")
        bh = self.bela_hash()
        if bh:
            verify = lambda: self.bela_ok(pw or "", bh)
        else:
            verify = lambda: hmac.compare_digest(self._hash(pw or "", bytes.fromhex(self.cfg["salt"])), bytes.fromhex(self.cfg["hash"]))
        if not self.check_password(ip, verify):
            raise ValueError("Passwort falsch")
        tok = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.sessions = {t: e for t, e in self.sessions.items() if e > now}
            self.sessions[tok] = now + SESSION_SECONDS
            if remember:
                keep = {k: v for k, v in self._remembered().items()
                        if isinstance(v, list) and len(v) == 2 and v[0] > now and v[1] == self._cred()}
                keep[self._tok_id(tok)] = [now + REMEMBER_SECONDS, self._cred()]
                try:
                    self._save_remembered(keep)
                except OSError:
                    pass          # dann gilt die Sitzung wie ohne Haken
        return tok

    def valid(self, tok):
        if not tok:
            return False
        with self.lock:
            if self.sessions.get(tok, 0) > time.time():
                return True
            e = self._remembered().get(self._tok_id(tok))
            return bool(isinstance(e, list) and len(e) == 2 and e[0] > time.time() and e[1] == self._cred())

    def logout(self, tok):
        with self.lock:
            self.sessions.pop(tok or "", None)
            r = self._remembered()
            if r.pop(self._tok_id(tok or ""), None) is not None:
                try:
                    self._save_remembered(r)
                except OSError:
                    pass


KEY_PACKAGES = ("belabox-linux-rk3588", "belaui", "belacoder", "belabox-rk3588")


def belacoder_running():
    try:
        for d in os.listdir("/proc"):
            if d.isdigit() and (read(f"/proc/{d}/comm", "") or "").strip() == "belacoder":
                return True
    except OSError:
        pass
    return False


def installed_versions():
    """Versionen wichtiger Pakete aus /var/lib/dpkg/status (nur lesend)."""
    try:
        st = os.stat("/var/lib/dpkg/status")
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    hit = _TTL.get("dpkg")
    if hit is not None and hit[0] == sig and sig is not None:
        return dict(hit[1])
    res = {}
    for block in (read("/var/lib/dpkg/status", "") or "").split("\n\n"):
        f = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(" "))
        if f.get("Package") in KEY_PACKAGES and "installed" in f.get("Status", "") and "not-installed" not in f.get("Status", ""):
            res[f["Package"]] = f.get("Version", "?")
    _TTL["dpkg"] = (sig, dict(res))
    return res


VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}(-[a-z0-9.]{1,16})?$")


def vkey(v):
    """Sortierschlüssel einer Versionsnummer (Release vor Vorabversion)."""
    core, _, pre = v.partition("-")
    return tuple(int(x) for x in core.split(".")), (1, ()) if not pre else (0, tuple(pre.split(".")))


class Remote:
    """Fernzugriff über Tailscale. Lesen darf dieser Dienst (tailscale status), Verändern macht der Root-Helfer
    pipbox-remote.py über eine Auslösedatei mit einem Stichwort aus fester Liste. Funnel (öffentlich im Internet) gibt es nur auf
    ausdrückliche Anforderung (funnel_on mit Bestätigung "public"); sie hat keine Zeitgrenze und bleibt auch nach einem Neustart der
    Box bis zum Beenden bestehen."""
    STATUS = "/run/pipbox-remote/status.json"
    ACTIONS = ("install", "login", "down", "serve_on", "serve_off", "funnel_on", "funnel_off", "logout")

    def __init__(self, state_dir, demo):
        self.req = os.path.join(state_dir, "remote-request")
        self.demo = demo
        self.fake = {}

    def _ts(self, *args):
        """tailscale-Abfrage, 20 s zwischengespeichert (das Go-Programm zu starten kostet spürbar CPU). Ändert der Root-Helfer
        etwas (seine Statusdatei bekommt eine neue Zeit), gilt der Speicher als abgelaufen."""
        try:
            sig = os.stat(self.STATUS).st_mtime_ns
        except OSError:
            sig = None
        key = ("ts", args)
        hit = _TTL.get(key)
        if hit is not None and hit[2] == sig and time.monotonic() - hit[0] < 20:
            return hit[1]
        try:
            r = subprocess.run(["tailscale", *args], capture_output=True, text=True, timeout=6)
            val = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            val = {}
        _TTL[key] = (time.monotonic(), val, sig)
        return val

    def status(self):
        if self.demo:
            d = {"installed": True, "backend": "Running", "connected": True, "name": "irl4you-box.demo.ts.net",
                 "ip": "100.64.0.1", "tailnet": "demo",
                 "serve": bool(self.fake.get("serve")), "url": "https://irl4you-box.demo.ts.net/" if self.fake.get("serve") else "",
                 "funnel": bool(self.fake.get("funnel")),
                 "state": "idle", "step": "", "message": self.fake.get("message", ""), "login_url": "",
                 "hint_url": "", "helper_installed": True}
            return d
        installed = bool(shutil.which("tailscale"))
        out = {"installed": installed, "backend": "", "connected": False, "name": "", "ip": "", "tailnet": "",
               "serve": False, "url": "", "funnel": False, "state": "idle", "step": "", "message": "", "login_url": "",
               "hint_url": "", "helper_installed": os.path.exists("/etc/systemd/system/pipbox-remote.path")}
        try:
            with open(self.STATUS) as f:
                h = json.load(f)
        except (OSError, ValueError):
            h = {}
        out.update(state=h.get("state", "idle"), step=h.get("step", ""), message=h.get("message", ""),
                   login_url=h.get("login_url", ""), hint_url=h.get("hint_url", ""))
        if h.get("time") and out["state"] not in ("working",) and time.time() - h["time"] > 6 * 3600:
            out["message"] = ""
        if not installed:
            return out
        d = self._ts("status", "--json")
        out["backend"] = d.get("BackendState", "")
        out["connected"] = out["backend"] == "Running"
        me = d.get("Self") or {}
        out["name"] = str(me.get("DNSName", "")).rstrip(".")
        ips = d.get("TailscaleIPs") or []
        out["ip"] = next((i for i in ips if ":" not in i), "")
        out["tailnet"] = (d.get("CurrentTailnet") or {}).get("Name", "")
        if d.get("AuthURL"):
            out["login_url"] = d["AuthURL"]
        if out["connected"]:
            out["login_url"] = ""
            sv = self._ts("serve", "status", "--json")
            web = sv.get("Web") or {}
            for host, cfg in web.items():
                for h2 in (cfg.get("Handlers") or {}).values():
                    if "127.0.0.1:%d" % 8780 in str(h2.get("Proxy", "")):
                        out["serve"], out["url"] = True, "https://" + host.replace(":443", "") + "/"
            out["funnel"] = any((sv.get("AllowFunnel") or {}).values())
        return out

    def request(self, action, confirm, public=False):
        if action not in self.ACTIONS:
            raise ValueError("Unbekannte Aktion")
        if confirm is not True:
            raise ValueError("Bestätigung fehlt")
        if action == "funnel_on" and public is not True:
            raise ValueError("Die öffentliche Freigabe braucht eine ausdrückliche Bestätigung")
        st = self.status()
        if not st["helper_installed"]:
            raise ValueError("Der Fernzugriff-Helfer ist nicht installiert (install.sh erneut ausführen)")
        if st["state"] == "working":
            raise ValueError("Es läuft schon eine Aktion")
        if action == "install" and st["installed"]:
            raise ValueError("Tailscale ist schon installiert")
        if action != "install" and not st["installed"]:
            raise ValueError("Tailscale ist noch nicht installiert")
        if action == "funnel_on" and not st["connected"]:
            raise ValueError("Zuerst mit Tailscale verbinden")
        if action == "funnel_off" and not st["funnel"]:
            raise ValueError("Die öffentliche Freigabe ist nicht an")
        if self.demo:
            if action == "serve_on":
                self.fake = {"serve": True, "message": "Demo: freigegeben."}
            elif action == "serve_off":
                self.fake = {"serve": False, "message": "Demo: beendet."}
            elif action == "funnel_on":
                self.fake = {"serve": True, "funnel": True, "message": "Demo: öffentlich freigegeben."}
            elif action == "funnel_off":
                self.fake = {"serve": True, "funnel": False, "message": "Demo: öffentliche Freigabe beendet."}
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n")


class DeviceNames:
    """Eigene Namen für WLAN- und Bluetooth-Sticks (der Stick kennt seinen Handelsnamen, zum Beispiel Logilink, meist nicht). Schlüssel: die USB-Kennung
    ("usb:0bda:c811"; zwei gleiche Sticks teilen sich den Namen), sonst die Schnittstelle ("if:wlan0") oder die Adresse des Bluetooth-Adapters
    ("bt:AA:BB:CC:DD:EE:FF"). Die Datei steht im Zustandsordner; es sind keine Geheimnisse."""
    KEY_RE = re.compile(r"^(usb:[0-9a-f]{4}:[0-9a-f]{4}|if:[a-z0-9]{2,15}|net:[a-z0-9]{2,15}|bt:([0-9A-F]{2}:){5}[0-9A-F]{2})$")

    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, "device-names.json")
        self.lock = threading.Lock()

    def _all(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            return {k: v for k, v in d.items() if isinstance(k, str) and self.KEY_RE.match(k) and isinstance(v, str)} if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def key(usb_id="", iface="", address=""):
        if usb_id:
            return "usb:" + usb_id
        if iface:
            return "if:" + iface
        return "bt:" + address.upper() if address else ""

    def label(self, key, default):
        return self._all().get(key) or default

    def conn_names(self):
        """Eigene Namen der Verbindungen (Netzwerkschnittstellen), ergänzend zur Bezeichnung: {Schnittstelle: Name}. Schlüssel "net:<Schnittstelle>"."""
        return {k[4:]: v for k, v in self._all().items() if k.startswith("net:") and v}

    def set(self, key, name):
        """Name setzen (leer = Standardname). Prüft Schlüssel und Name streng."""
        if not isinstance(key, str) or not self.KEY_RE.match(key):
            raise ValueError("Gerät unbekannt")
        if not isinstance(name, str):
            raise ValueError("Name ungültig")
        name = " ".join(name.split())
        if len(name) > 40 or any(not ch.isprintable() for ch in name):
            raise ValueError("Name: höchstens 40 Zeichen, keine Sonderzeichen")
        with self.lock:
            d = self._all()
            if name:
                d[key] = name
            else:
                d.pop(key, None)
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
            with os.fdopen(fd, "w") as f:
                json.dump(d, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)


def label_bluetooth(st, names):
    """Anzeigenamen der Bluetooth-Adapter und der Sticks ohne Adapter in der Antwort des Bluetooth-Dienstes: eigener Name (names), sonst der
    Standardname aus der Meldung des Sticks (Hersteller davor, wenn der Name nur eine Standardbezeichnung ist)."""
    for a in st.get("adapters") or []:
        a["key"] = DeviceNames.key(a.get("usb_id", ""), "", a.get("address", ""))
        default = dji.device_label(a.get("name", ""), a.get("vendor", ""), a.get("usb_id", "")) or ("Eingebauter Bluetooth-Adapter" if not a.get("usb_id") else "Bluetooth-Stick")
        a["label"] = names.label(a["key"], default) if names and a["key"] else default
        a["custom"] = a["label"] != default
    for x in st.get("adapter_problems") or []:
        x["key"] = DeviceNames.key(x.get("id", ""))
        default = dji.device_label(x.get("name", ""), "", x.get("id", ""))
        x["label"] = names.label(x["key"], default) if names and x["key"] else default
    return st


class Wifi:
    """WLAN-Verbindung (z. B. Handy-Hotspot als weiterer Sendeweg) und WLAN-Hotspot der Box. Lesen darf dieser Dienst, Verbinden macht der
    Root-Helfer pipbox-wifi.py über eine Auslösedatei (0600, wird dort sofort gelöscht). Das Passwort eines WLANs, mit dem sich die Box
    verbindet, liegt nur dort und wird nie gespeichert oder ausgegeben; nmcli legt das Profil an. Das Passwort des eigenen Hotspots ist
    zum Weitergeben an Kameras und Handys gedacht und liegt in hotspot.json (Benutzer pipbox, 0600)."""
    STATUS = "/run/pipbox-wifi/status.json"
    DRIVER_STATUS = "/run/pipbox-wlandriver/status.json"        # schreibt der Root-Helfer pipbox-wlandriver.py (Treiber für WLAN-Sticks mit AIC8800D80)
    ACTIONS = ("scan", "connect", "forget", "disconnect", "hotspot_start", "hotspot_stop", "hotspot_save")
    HS_PREFIX = "pipbox-hotspot-"
    HS_BANDS = {"bg": tuple(range(1, 14)), "a": (36, 40, 44, 48)}

    def __init__(self, state_dir, demo, netchoice, names=None, srtla=None):
        self.req = os.path.join(state_dir, "wifi-request")
        self.hs_file = os.path.join(state_dir, "hotspot.json")
        self.demo, self.netchoice, self.names, self.srtla = demo, netchoice, names, srtla
        self.fake_hs = {}
        self.fake_msg = ("", "")

    def hotspots(self):
        """Gespeicherte Hotspot-Einstellungen {Karte: {ssid, password, band, channel}}."""
        if self.demo:
            return dict(self.fake_hs)
        try:
            with open(self.hs_file) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(d, dict) else {}

    def hotspot_secret(self, iface):
        """Name und Passwort des Hotspots dieser Karte (nur für die angemeldete Oberfläche, auf Knopfdruck)."""
        h = self.hotspots().get(iface if isinstance(iface, str) else "")
        if not h or not h.get("password"):
            raise ValueError("Auf dieser Karte ist kein Hotspot gespeichert")
        return {"iface": iface, "ssid": str(h.get("ssid", "")), "password": str(h["password"])}

    @staticmethod
    def caps(iface):
        """Was die Karte kann (NetworkManager): Zugangspunkt, 2,4 und 5 GHz. Fünf Minuten zwischengespeichert."""
        def raw():
            try:
                r = subprocess.run(["nmcli", "-g", "WIFI-PROPERTIES.AP,WIFI-PROPERTIES.2GHZ,WIFI-PROPERTIES.5GHZ", "dev", "show", iface],
                                   capture_output=True, text=True, timeout=5)
                v = [x.strip().lower() == "yes" for x in r.stdout.splitlines()]
            except (OSError, subprocess.TimeoutExpired):
                return None
            return v + [False] * (3 - len(v)) if v else None
        v = ttl_cached("wifi_caps_" + iface, 300.0, raw)
        return {"ap": v[0], "band24": v[1], "band5": v[2]} if v else {"ap": None, "band24": None, "band5": None}

    def cards(self):
        out = []
        try:
            names = sorted(os.listdir("/sys/class/net"))
        except OSError:
            return out
        ips = {o["iface"]: o["ip"] for o in iface_ips()}
        hs = self.hotspots()
        for n in names:
            if os.path.isdir(f"/sys/class/net/{n}/wireless") and not n.startswith("p2p"):
                info = dji.netdev_info(n)                          # Name des WLAN-Sticks (z. B. "802.11ac NIC"), USB-Kennung, Treiber
                out.append({"iface": n, "ip": ips.get(n, ""), "camera_net": n == self.netchoice.iface,
                            "up": (read(f"/sys/class/net/{n}/operstate", "") or "").strip() == "up",
                            "ssid": "", "signal": None, "name": info["name"], "vendor": info["vendor"],
                            "usb_id": info["usb_id"], "driver": info["driver"], **self.caps(n)})
                self._name(out[-1])
        if out:                                        # Name und Signal des verbundenen Netzes (Profilname = SSID)
            try:
                r = subprocess.run(["nmcli", "-t", "-f", "DEVICE,CONNECTION", "dev"], capture_output=True, text=True, timeout=4)
                for line in r.stdout.splitlines():
                    dev, _, con = line.partition(":")
                    con = con.replace("\\:", ":")
                    for c in out:
                        if c["iface"] == dev and c["ip"]:
                            if con == self.HS_PREFIX + dev:        # der Hotspot dieser Box: kein Netz, mit dem die Karte verbunden ist
                                c["hotspot_running"] = True
                            else:
                                c["ssid"] = con
                for c in out:
                    if c["ssid"]:
                        r = subprocess.run(["nmcli", "-t", "-f", "IN-USE,SIGNAL", "dev", "wifi", "list", "ifname", c["iface"],
                                            "--rescan", "no"], capture_output=True, text=True, timeout=4)
                        for line in r.stdout.splitlines():
                            if line.startswith("*:") and line[2:].isdigit():
                                c["signal"] = int(line[2:])
            except (OSError, subprocess.TimeoutExpired):
                pass
        for c in out:
            h = hs.get(c["iface"])
            c["hotspot"] = ({"ssid": str(h.get("ssid", "")), "band": h.get("band", "bg"), "channel": h.get("channel", 0),
                             "running": bool(c.pop("hotspot_running", False))} if h else None)
            c.pop("hotspot_running", None)
        return out

    def _name(self, card):
        """Schlüssel und Anzeigename der Karte: eigener Name, sonst der Standardname aus der Meldung des Sticks."""
        card["key"] = DeviceNames.key(card.get("usb_id", ""), card["iface"])
        default = dji.device_label(card.get("name", ""), card.get("vendor", ""), card.get("usb_id", ""))
        card["label"] = self.names.label(card["key"], default) if self.names else default
        card["custom"] = bool(self.names and card["label"] != default)

    SYS_NET = "/sys/class/net"

    def labels(self):
        """{Schnittstelle: Anzeigename} der WLAN-Karten für die Anzeige im Status ("wlan0 (TP-Link Archer T2U)"): eigener Name der Karte, sonst der Name,
        den der Stick meldet. Leicht (nur /sys, der Stand der Sticks 10 s gemerkt), weil die Statusseite oft fragt."""
        if self.demo:
            return {"wlan0": dji.device_label("802.11ac NIC", "Realtek", "0bda:c811"), "wlan1": dji.device_label("802.11ac NIC", "Realtek", "2357:011e")}

        def raw():
            out = []
            try:
                names = sorted(os.listdir(self.SYS_NET))
            except OSError:
                return out
            for n in names:
                if os.path.isdir(f"{self.SYS_NET}/{n}/wireless") and not n.startswith("p2p"):
                    info = dji.netdev_info(n, self.SYS_NET)
                    out.append((n, info["usb_id"], dji.device_label(info["name"], info["vendor"], info["usb_id"])))
            return out
        res = {}
        for n, usb_id, default in ttl_cached("wifi_labels", 10.0, raw) or []:
            res[n] = (self.names.label(DeviceNames.key(usb_id, n), default) if self.names else default) or ""
        return {n: v for n, v in res.items() if v}

    def driver_status(self):
        """Was der Treiber-Helfer für WLAN-Sticks gerade tut ("working", "waiting", "failed", "unsupported", "ok") und sagt, sonst leer."""
        try:
            with open(self.DRIVER_STATUS) as f:
                raw = json.load(f)
            return {"state": str(raw.get("state", "")), "message": str(raw.get("message", ""))[:300]}
        except (OSError, ValueError, AttributeError):
            return {}

    def status(self):
        if self.demo:
            cards = []
            for iface, ip, ssid, usb in (("wlan0", "10.0.0.5", "Demo-Hotspot", "0bda:c811"), ("wlan1", "", "", "2357:011e")):
                c = {"iface": iface, "ip": ip, "camera_net": False, "up": bool(ip), "ssid": ssid, "signal": 80 if ip else None,
                     "name": "802.11ac NIC", "vendor": "Realtek", "usb_id": usb, "driver": "rtl8821cu", "ap": True, "band24": True, "band5": True}
                h = self.fake_hs.get(iface)
                if h and h.get("running", True):
                    c.update(ip="10.42.0.1", ssid="", signal=None, up=True)
                c["hotspot"] = {"ssid": h["ssid"], "band": h["band"], "channel": h["channel"], "running": h.get("running", True)} if h else None
                self._name(c)
                cards.append(c)
            return {"helper_installed": True, "cards": cards,
                    "state": "done" if self.fake_msg[0] else "idle", "message": self.fake_msg[1], "action": self.fake_msg[0], "scan": {"iface": "wlan0", "nets": [
                        {"ssid": "Demo-Hotspot", "signal": 80, "security": "WPA2", "in_use": False},
                        {"ssid": "Mein Handy", "signal": 62, "security": "WPA2 WPA3", "in_use": False},
                        {"ssid": "Gast", "signal": 31, "security": "offen", "in_use": False}]}, "saved": []}
        try:
            with open(self.STATUS) as f:
                h = json.load(f)
        except (OSError, ValueError):
            h = {}
        return {"helper_installed": os.path.exists("/etc/systemd/system/pipbox-wifi.path"), "cards": self.cards(),
                "state": h.get("state", "idle"), "message": h.get("message", ""), "action": h.get("action", ""), "scan": h.get("scan") or {},
                "saved": h.get("saved") or [], "time": h.get("time", 0), "driver": self.driver_status()}

    def _hotspot_request(self, d, st, card):
        """Prüft Starten, Beenden und Speichern der Einstellungen eines Hotspots; gibt die Anfrage für den Helfer zurück. Fehlen bei "hotspot_start"
        Name und Co., gelten die gespeicherten Einstellungen dieser Karte (Schalter "Hotspot-Modus")."""
        action, iface = d.get("action"), card["iface"]
        req = {"action": action, "iface": iface}
        running = bool((card.get("hotspot") or {}).get("running"))
        saved = self.hotspots().get(iface) or {}
        if action == "hotspot_stop":
            if not (card.get("hotspot") or {}):
                raise ValueError("Auf dieser Karte ist kein Hotspot eingerichtet")
            return req
        if action == "hotspot_save" and running:
            raise ValueError("Der Hotspot läuft: bitte zuerst ausschalten, dann einstellen")
        if action == "hotspot_start" and d.get("ssid") is None:
            if not saved.get("ssid") or not saved.get("password"):
                raise ValueError("Bitte zuerst „Einstellen“: Name und Passwort des Hotspots festlegen")
            d = dict(d, ssid=saved["ssid"], password="", band=saved.get("band", "bg"), channel=saved.get("channel", 0))
        ssid = d.get("ssid")
        if (not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or ssid != ssid.strip()
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in ssid) or ssid.startswith(self.HS_PREFIX)):
            raise ValueError("Name des Hotspots: 1 bis 32 Zeichen, keine Leerzeichen am Anfang oder Ende")
        pw = d.get("password", "")
        if not isinstance(pw, str):
            raise ValueError("Passwort ungültig")
        if pw == "":
            if not saved.get("password"):
                raise ValueError("Bitte ein Passwort vergeben (8 bis 63 Zeichen)")
        elif not 8 <= len(pw) <= 63 or any(not 32 <= ord(ch) < 127 for ch in pw):
            raise ValueError("Passwort: 8 bis 63 Zeichen, nur Buchstaben, Ziffern und Satzzeichen ohne Umlaute")
        band = d.get("band", "bg")
        if band not in self.HS_BANDS:
            raise ValueError("Band: 2,4 GHz oder 5 GHz")
        ch = d.get("channel", 0)
        if isinstance(ch, bool) or not isinstance(ch, int) or (ch != 0 and ch not in self.HS_BANDS[band]):
            raise ValueError("Kanal passt nicht zum Band")
        if card.get("ap") is False:
            raise ValueError("Diese WLAN-Karte kann keinen Hotspot aufbauen")
        if band == "a" and card.get("band5") is False:
            raise ValueError("Diese WLAN-Karte kann kein 5 GHz")
        if action == "hotspot_start" and card.get("ip") and not running and d.get("confirm") is not True:
            raise ValueError("Bestätigung fehlt: Die Karte ist gerade mit einem WLAN verbunden, diese Verbindung wird beendet")
        req.update(ssid=ssid, password=pw, band=band, channel=ch)
        return req

    def request(self, d):
        action = d.get("action")
        if action not in self.ACTIONS:
            raise ValueError("Unbekannte Aktion")
        st = self.status()
        if not st["helper_installed"]:
            raise ValueError("Der WLAN-Helfer ist nicht installiert (install.sh erneut ausführen)")
        if st["state"] == "working" and time.time() - st.get("time", 0) < 90:
            raise ValueError("Es läuft schon eine Aktion")
        iface = str(d.get("iface", ""))
        card = None
        if action in ("scan", "connect", "disconnect") or action.startswith("hotspot_"):
            card = next((c for c in st["cards"] if c["iface"] == iface), None)
            if not card:
                raise ValueError("Diese WLAN-Karte gibt es nicht")
            if card["camera_net"]:
                raise ValueError("Diese Karte ist das Kameranetz")
            if action in ("scan", "connect", "disconnect") and (card.get("hotspot") or {}).get("running"):
                raise ValueError("Auf dieser Karte läuft ein Hotspot. Bitte zuerst den Hotspot beenden.")
        req = {"action": action, "iface": iface}
        if action.startswith("hotspot_"):
            req = self._hotspot_request(d, st, card)
        if action in ("connect", "forget"):
            ssid = d.get("ssid")
            if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
                raise ValueError("Netzname fehlt oder ist zu lang")
            if ssid.startswith(self.HS_PREFIX):
                raise ValueError("Dieser Netzname ist für den Hotspot der Box reserviert")
            req["ssid"] = ssid
        if action == "connect":
            pw = d.get("password", "")
            if not isinstance(pw, str) or len(pw) > 64:
                raise ValueError("Passwort ungültig")
            req.update(password=pw, hidden=d.get("hidden") is True)
        if action == "hotspot_start":
            self._leave_uplinks(iface)
        if self.demo:
            if action in ("hotspot_start", "hotspot_save"):
                run = action == "hotspot_start"
                self.fake_hs[iface] = {"ssid": req["ssid"], "password": req["password"] or (self.fake_hs.get(iface) or {}).get("password", ""),
                                       "band": req["band"], "channel": req["channel"], "running": run}
                self.fake_msg = (action, ("Hotspot „%s“ läuft auf %s (Vorschau)" % (req["ssid"], iface)) if run else "Hotspot-Einstellungen gespeichert (Vorschau)")
            elif action == "hotspot_stop" and iface in self.fake_hs:
                self.fake_hs[iface]["running"] = False
                self.fake_msg = (action, "Hotspot beendet (Vorschau)")
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(req, f)

    EXPORT_MAX = 262144

    def helper_call(self, req, wait=40):
        """Eine Aktion für den Root-Helfer auslösen und auf sein Ergebnis warten (status.json mit unserer Marke)."""
        st = self.status()
        if not st["helper_installed"]:
            raise RuntimeError("Der WLAN-Helfer ist nicht installiert (install.sh erneut ausführen)")
        if st["state"] == "working" and time.time() - st.get("time", 0) < 90:
            raise ValueError("Es läuft schon eine WLAN-Aktion")
        mark = secrets.token_hex(8)
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(dict(req, mark=mark), f)
        end = time.monotonic() + wait
        while time.monotonic() < end:
            time.sleep(0.4)
            try:
                with open(self.STATUS) as f:
                    h = json.load(f)
            except (OSError, ValueError):
                continue
            if h.get("mark") == mark and h.get("state") in ("done", "error"):
                return h
        raise RuntimeError("Der WLAN-Helfer hat nicht rechtzeitig geantwortet")

    def export_saved(self, with_passwords):
        """Gespeicherte WLAN-Netze samt Passwort (nur wenn gewünscht) über den Helfer lesen: {"networks": [...], "skipped": [...]}."""
        if self.demo:
            return {"networks": [{"ssid": "Demo-Hotspot", "hidden": False, "open": False, "password": "demo-passwort-1" if with_passwords else ""},
                                 {"ssid": "Gast", "hidden": False, "open": True, "password": ""}], "skipped": []}
        h = self.helper_call({"action": "export_wifi", "secrets": bool(with_passwords)})
        path = os.path.join(os.path.dirname(self.req), "wifi-export.json")
        if h.get("state") == "error":
            try:
                os.unlink(path)
            except OSError:
                pass
            raise RuntimeError(h.get("message") or "Der WLAN-Helfer meldet einen Fehler")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("keine normale Datei")
                raw = os.read(fd, self.EXPORT_MAX + 1)
            finally:
                os.close(fd)
            os.unlink(path)                                    # die Datei enthält Passwörter: sofort wieder weg
        except OSError:
            raise RuntimeError(h.get("message") or "Der WLAN-Helfer hat keine Liste geliefert")
        try:
            d = json.loads(raw.decode("utf-8")) if len(raw) <= self.EXPORT_MAX else None
        except ValueError:
            d = None
        if not isinstance(d, dict) or not isinstance(d.get("networks"), list):
            raise RuntimeError("Die Liste des WLAN-Helfers ist ungültig")
        return {"networks": [n for n in d["networks"] if isinstance(n, dict)], "skipped": [x for x in d.get("skipped", []) if isinstance(x, dict)]}

    def import_saved(self, networks):
        """Gespeicherte WLAN-Netze über den Helfer anlegen (er prüft noch einmal und ersetzt gleichnamige Profile)."""
        if not networks:
            return "Keine WLAN-Netze zum Einspielen"
        if self.demo:
            return "%d WLAN-Netze eingespielt (Vorschau)" % len(networks)
        h = self.helper_call({"action": "import_wifi", "networks": networks})
        if h.get("state") == "error":
            raise RuntimeError(h.get("message") or "Der WLAN-Helfer meldet einen Fehler")
        return h.get("message") or "WLAN-Netze eingespielt"

    def hotspot_import(self, entries):
        """Hotspot-Einstellungen (Name, Passwort, Band, Kanal je Karte) aus einer Sicherung in hotspot.json übernehmen; die Hotspots selbst startet niemand.
        Fehlt in der Sicherung das Passwort, bleibt das gespeicherte dieser Karte; ohne eines gibt es keinen Eintrag."""
        cur = self.hotspots()
        out, skipped = dict(cur), []
        for iface, h in entries.items():
            pw = h.get("password") or (cur.get(iface) or {}).get("password", "")
            if not pw:
                skipped.append(iface)
                continue
            out[iface] = {"ssid": h["ssid"], "password": pw, "band": h["band"], "channel": h["channel"]}
        if self.demo:
            for iface, h in out.items():
                self.fake_hs[iface] = dict(h, running=(self.fake_hs.get(iface) or {}).get("running", False))
        else:
            tmp = self.hs_file + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(out, f)
            os.replace(tmp, self.hs_file)
        n = len(entries) - len(skipped)
        return "%d Hotspots eingespielt" % n + ((", ohne Passwort ausgelassen: " + ", ".join(skipped)) if skipped else "")

    def _leave_uplinks(self, iface):
        """Ein Hotspot hat keinen Weg ins Internet: Die Karte darf danach kein Netz zum Senden mehr sein. Ist sie das einzige, wird nichts gestartet."""
        if self.srtla is None:
            return
        ups = list(((self.srtla.data or {}).get("settings") or {}).get("uplinks") or [])
        if iface not in ups:
            return
        rest = [u for u in ups if u != iface]
        valid = [o["iface"] for o in iface_ips()]
        if not any(u in valid for u in rest):
            raise ValueError("Diese Karte ist gerade das einzige Netz zum Senden. Bitte zuerst ein anderes Netz zum Senden wählen.")
        self.srtla.set_settings({"uplinks": rest}, valid or ["eth0", "eth1"])


class Power:
    """Box herunterfahren oder neu starten. Dieser Dienst hat keine Root-Rechte: er legt nur ein Stichwort aus fester Liste
    in eine Auslösedatei, der Root-Helfer pipbox-power.py führt es aus."""
    ACTIONS = ("poweroff", "reboot")

    def __init__(self, state_dir, demo, send):
        self.req = os.path.join(state_dir, "power-request")
        self.demo, self.send = demo, send

    def status(self):
        sending = False if self.demo else bool(belacoder_running() or self.send._active())
        return {"helper_installed": self.demo or os.path.exists("/etc/systemd/system/pipbox-power.path"), "sending": sending}

    def request(self, action, confirm):
        if action not in self.ACTIONS:
            raise ValueError("Unbekannte Aktion")
        if confirm is not True:
            raise ValueError("Bestätigung fehlt")
        if not self.status()["helper_installed"]:
            raise ValueError("Der Helfer ist nicht installiert (install.sh erneut ausführen)")
        if self.demo:
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n")


class AutoStart:
    """Automatisch live gehen, nachdem die Box gestartet wurde (Einstellung, standardmäßig aus).

    Nur einmal pro Start der Box: Nach dem Hochfahren wartet die Box, bis ein SRTLA-Server gewählt ist und mindestens eine
    Kamera sendet (dieselben Voraussetzungen wie "Live gehen"), und startet dann genau so, als wäre "Live gehen" gedrückt worden.
    "Live beenden" vor dem Start bricht ab; ein Neustart der Oberfläche (zum Beispiel durch ein Update) startet die Sendung
    nicht noch einmal. Findet sich innerhalb von WAIT_S Sekunden keine Kamera, gibt die Box auf und zeigt den Grund."""
    WAIT_S = 600
    RETRY_S = 20

    def __init__(self, state_dir, send, demo=False, wait_s=None, poll_s=5.0):
        self.path = os.path.join(state_dir, "autostart.json")
        self.send, self.demo = send, demo
        self.wait_s = self.WAIT_S if wait_s is None else wait_s
        self.poll_s = poll_s
        self.lock = threading.Lock()
        self.cancel_ev = threading.Event()
        self.data = {"enabled": False, "boot": ""}
        try:
            with open(self.path) as f:
                d = json.load(f)
            self.data["enabled"] = d.get("enabled") is True
            self.data["boot"] = str(d.get("boot", ""))[:64]
        except (OSError, ValueError):
            pass
        self.phase, self.message, self.until = "aus", "", None

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    @staticmethod
    def boot_id():
        return (read("/proc/sys/kernel/random/boot_id", "demo") or "demo").strip()

    def enabled(self):
        return self.data["enabled"]

    def set_enabled(self, on):
        if not isinstance(on, bool):
            raise ValueError("ja oder nein")
        with self.lock:
            self.data["enabled"] = on
            self._save()
        if not on:
            self.cancel("ausgeschaltet")

    def cancel(self, why="abgebrochen"):
        """Wartet die Box noch auf den Automatik-Start, wird er für diesen Start der Box abgebrochen."""
        with self.lock:
            if self.phase == "wartet":
                self.cancel_ev.set()
                self.phase, self.message = "abgebrochen", why
                self.data["boot"] = self.boot_id()
                self._save()

    def status(self):
        return {"enabled": self.data["enabled"], "phase": self.phase, "message": self.message, "until": self.until}

    def _finish(self, phase, message=""):
        with self.lock:
            if self.phase == "wartet":
                self.phase, self.message = phase, message
            self.data["boot"] = self.boot_id()
            self._save()

    def run(self):
        """Läuft einmal im Hintergrund, solange die Oberfläche läuft."""
        if not self.data["enabled"]:
            return
        if self.data["boot"] == self.boot_id():
            with self.lock:
                self.phase = "erledigt"
            return
        with self.lock:
            self.phase, self.message = "wartet", "Wartet auf eine sendende Kamera"
            self.until = time.time() + self.wait_s
        deadline = time.monotonic() + self.wait_s
        last_try, reasons = None, []       # None: noch kein Versuch (die Uhr zählt ab Hochfahren, 0 wäre nicht 'lange her')
        while time.monotonic() < deadline and not self.cancel_ev.is_set():
            try:
                st = self.send.status()
            except Exception:
                st = {}
            if st.get("active"):
                self._finish("gestartet", "Läuft")
                return
            reasons = st.get("reasons") or reasons
            if st.get("can_start") and (last_try is None or time.monotonic() - last_try >= self.RETRY_S):
                last_try = time.monotonic()
                try:
                    self.send.request("start", True)
                    with self.lock:
                        self.message = "Startet"
                except Exception as e:
                    reasons = [str(e)]
            self.cancel_ev.wait(self.poll_s)
        if self.cancel_ev.is_set():
            return
        self._finish("aufgegeben", "Automatischer Start aufgegeben: " + ("; ".join(reasons) if reasons else "keine sendende Kamera"))


class LogMode:
    """Protokoll-Modus: "sparsam" (Journal und Zustandsprotokoll nur im Arbeitsspeicher, schont die Speicherkarte, nach einem
    Absturz bleibt keine Spur) oder "ausfuehrlich" (dauerhaft, zur Fehlersuche). Dieser Dienst hat keine Root-Rechte: er legt
    nur ein Stichwort aus fester Liste in eine Auslösedatei, der Root-Helfer pipbox-logmode.py stellt um."""
    MODES = ("sparsam", "ausfuehrlich")
    FILE = "/etc/pipbox/logmode"

    def __init__(self, state_dir, demo):
        self.req = os.path.join(state_dir, "logmode-request")
        self.demo = demo
        self.fake = "ausfuehrlich"

    def status(self):
        if self.demo:
            return {"mode": self.fake, "helper_installed": True}
        mode = (read(self.FILE, "") or "").strip()
        # Ohne Datei (ältere Installation) schreibt alles wie bisher dauerhaft: das entspricht "ausfuehrlich"
        return {"mode": mode if mode in self.MODES else "ausfuehrlich",
                "helper_installed": os.path.exists("/etc/systemd/system/pipbox-logmode.path")}

    def request(self, mode):
        if mode not in self.MODES:
            raise ValueError("Unbekannter Modus")
        if not self.status()["helper_installed"]:
            raise ValueError("Der Helfer ist nicht installiert (Software-Update einspielen oder install.sh erneut ausführen)")
        if self.demo:
            self.fake = mode
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(mode + "\n")


class LogBundle:
    """Protokolle zum Herunterladen: eine einzige Textdatei mit den Journalen der IRL4YOU-Dienste, dem Zustandsprotokoll und
    den Einstellungen, von Passwörtern, Stream-ID, WLAN-Namen und Adressen bereinigt. Dieser Dienst hat keine Root-Rechte und
    kann die Journale nicht lesen: er legt nur das Stichwort "collect" in eine Auslösedatei, der Root-Helfer pipbox-logs.py
    sammelt und legt das Ergebnis nach /run/pipbox-logs (nur root darf dort schreiben)."""
    DIR = "/run/pipbox-logs"
    FILE = DIR + "/bundle.txt"
    STATUS = DIR + "/status.json"
    HELPER = "/etc/systemd/system/pipbox-logs.path"
    MAX_BYTES = 4_000_000
    WAIT_SECONDS = 150                 # so lange darf das Sammeln dauern, bevor der Helfer als nicht erreichbar gilt

    def __init__(self, state_dir, demo):
        self.req = os.path.join(state_dir, "logs-request")
        self.demo = demo
        self.requested = 0.0
        self.fake = None

    def installed(self):
        return self.demo or os.path.exists(self.HELPER)

    def status(self):
        out = {"helper_installed": self.installed(), "state": "idle", "message": "", "size": 0}
        if self.demo:
            if self.fake:
                out.update(state="done", message="Fertig", size=len(self.fake.encode()), time=int(self.requested))
            return out
        try:
            with open(self.STATUS) as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = None
        waiting = self.requested and time.time() - self.requested < self.WAIT_SECONDS
        if not isinstance(st, dict) or (self.requested and int(st.get("time", 0) or 0) < int(self.requested)):
            # Der Helfer hat sich zu dieser Anfrage noch nicht gemeldet: ein altes Ergebnis zählt nicht
            if self.requested:
                out["state"], out["message"] = ("working", "Warte auf den Helfer …") if waiting else ("error", "Keine Antwort vom Helfer")
            return out
        state = st.get("state") if st.get("state") in ("working", "done", "error") else "error"
        out.update(state=state, message=str(st.get("message", ""))[:200], time=int(st.get("time", 0) or 0))
        if state == "done":
            try:
                out["size"] = os.stat(self.FILE).st_size
            except OSError:
                out["state"], out["message"] = "error", "Datei fehlt"
        elif state == "working" and not waiting:
            out["state"], out["message"] = "error", "Zeitüberschreitung beim Sammeln"
        return out

    def request(self):
        if not self.installed():
            raise ValueError("Der Helfer ist nicht installiert (Software-Update einspielen oder install.sh erneut ausführen)")
        if self.status()["state"] == "working" and time.time() - self.requested < self.WAIT_SECONDS:
            return                                            # läuft schon
        self.requested = time.time()
        if self.demo:
            self.fake = ("IRL4YOU BOX Protokolle (Vorschau)\n\n===== Journal pipbox-dji =====\n"
                         "2026-10-04T17:21:22+0000 INFO pipbox-dji: <MAC-1 AC:DE:48>: Statusnachricht\n")
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("collect\n")

    def content(self):
        """Inhalt der fertigen Datei oder None."""
        if self.demo:
            return self.fake.encode() if self.fake else None
        if self.status()["state"] != "done":
            return None
        try:
            with open(self.FILE, "rb") as f:
                return f.read(self.MAX_BYTES)
        except OSError:
            return None


class Developer:
    """Entwickler: den SSH-Dienst der Box ein- und ausschalten und das SSH-Passwort erzeugen (wie die Original-Oberfläche der BELABOX). Dieser
    Dienst hat keine Root-Rechte: er legt nur ein Stichwort aus fester Liste (start, stop, check, reset) in eine Auslösedatei, der Root-Helfer
    pipbox-ssh.py führt es aus. Schlüssel und die Einstellungen von SSH fasst nichts davon an. Das erzeugte Passwort liegt in ssh-pass.json
    (Benutzer pipbox, 0600) und wird nur auf Knopfdruck angezeigt, nie in der Statusabfrage. Von den Dateien der Original-Oberfläche wird nur
    gelesen, wie der SSH-Benutzer heißt und welches Passwort sie erzeugt hat."""
    ACTIONS = ("start", "stop", "check", "reset")
    STATUS = "/run/pipbox-ssh/status.json"
    HELPER = "/etc/systemd/system/pipbox-ssh.path"
    USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")

    def __init__(self, state_dir, demo, bela_config=None):
        self.req = os.path.join(state_dir, "ssh-request")
        self.pass_file = os.path.join(state_dir, "ssh-pass.json")
        self.demo = demo
        self.bela_dir = os.path.dirname(os.path.abspath(bela_config)) if bela_config else None
        self.fake_active = False
        self.fake_pass = "Demo-Passwort-1234"
        self.fake_time = 0

    def _json(self, name):
        if not self.bela_dir:
            return None
        try:
            with open(os.path.join(self.bela_dir, name)) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        return d if isinstance(d, dict) else None

    def ssh_user(self):
        u = (self._json("setup.json") or {}).get("ssh_user")
        return u if isinstance(u, str) and self.USER_RE.fullmatch(u) else ""

    def _ours(self):
        """Das von IRL4YOU BOX erzeugte Passwort (ssh-pass.json) für den SSH-Benutzer oder None."""
        try:
            with open(self.pass_file) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        user = self.ssh_user()
        if isinstance(d, dict) and isinstance(d.get("password"), str) and d["password"] and isinstance(d.get("user"), str) and (not user or d["user"] == user):
            return d
        return None

    def password_created(self):
        """Gibt es ein erzeugtes SSH-Passwort (von IRL4YOU BOX oder von der Original-Oberfläche)? None, wenn weder das eine noch die Original-Oberfläche
        da ist. Nur das Vorhandensein wird geprüft."""
        if self._ours():
            return True
        c = self._json("config.json")
        return None if c is None else bool(c.get("ssh_pass"))

    @staticmethod
    def _has_unit():
        try:
            return subprocess.run(["systemctl", "cat", "ssh.service"], capture_output=True, timeout=4).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _systemctl(*args):
        try:
            r = subprocess.run(["systemctl", *args, "ssh"], capture_output=True, text=True, timeout=4)
            return r.returncode, r.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return 1, ""

    def status(self):
        if self.demo:
            return {"helper_installed": True, "available": True, "user": "user", "can_reset": True, "active": self.fake_active, "enabled": True,
                    "password_created": bool(self.fake_pass), "password_state": "generated" if self.fake_pass else "unknown",
                    "state": "idle", "message": "", "time": self.fake_time}
        try:
            with open(self.STATUS) as f:
                h = json.load(f)
        except (OSError, ValueError):
            h = {}
        has_unit = ttl_cached("ssh_unit", 30.0, self._has_unit)
        active = ttl_cached("ssh_active", 2.0, lambda: self._systemctl("is-active")[1] == "active")
        enabled = ttl_cached("ssh_enabled", 30.0, lambda: self._systemctl("is-enabled")[1] == "enabled")
        user = self.ssh_user()
        return {"helper_installed": os.path.exists(self.HELPER), "available": bool(has_unit), "user": user, "can_reset": bool(user),
                "active": active, "enabled": enabled, "password_created": self.password_created(),
                "password_state": h.get("password_state") if h.get("password_state") in ("generated", "own", "unknown") else None,
                "state": h.get("state", "idle"), "message": str(h.get("message", ""))[:200], "time": h.get("time", 0)}

    def request(self, action, confirm):
        if action not in self.ACTIONS:
            raise ValueError("Unbekannte Aktion")
        st = self.status()
        if not st["helper_installed"]:
            raise ValueError("Der Helfer ist nicht installiert (Software-Update einspielen oder install.sh erneut ausführen)")
        if action != "reset" and action != "check" and not st["available"]:
            raise ValueError("Auf dieser Box gibt es keinen SSH-Dienst")
        if st["state"] == "working" and time.time() - st.get("time", 0) < 60:
            raise ValueError("Es läuft schon eine Aktion")
        if action == "reset":
            if not st["can_reset"]:
                raise ValueError("Für diese Box ist kein SSH-Benutzer eingerichtet")
            if confirm is not True:
                raise ValueError("Bestätigung fehlt: Das alte Passwort gilt danach nicht mehr")
        if self.demo:
            self.fake_time += 1
            if action in ("start", "stop"):
                self.fake_active = action == "start"
            elif action == "reset":
                self.fake_pass = "Neu-" + str(self.fake_time) + "-Demo-Passwort"
            return
        ttl_cached_drop("ssh_active")
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n")

    def password(self):
        """Das erzeugte SSH-Passwort (nur für die angemeldete Oberfläche, auf Knopfdruck): das von IRL4YOU BOX erzeugte, sonst das der Original-Oberfläche.
        Hier wird nichts erzeugt oder geändert."""
        if self.demo:
            if not self.fake_pass:
                raise ValueError("Für SSH wurde noch kein Passwort erzeugt")
            return {"user": "user", "password": self.fake_pass, "state": "generated"}
        ours = self._ours()
        if ours:
            return {"user": self.ssh_user() or ours["user"], "password": ours["password"], "state": self.status().get("password_state")}
        c = self._json("config.json")
        pw = c.get("ssh_pass") if c else None
        if not isinstance(pw, str) or not pw:
            raise ValueError("Für SSH wurde noch kein Passwort erzeugt. Es entsteht beim ersten Einschalten oder mit „Passwort zurücksetzen“.")
        return {"user": self.ssh_user(), "password": pw, "state": self.status().get("password_state")}


class SwUpdate:
    """Software-Update von IRL4YOU BOX aus dem eigenen GitHub-Repository.

    Dieser Dienst hat keine Root-Rechte. Er prüft nur, ob auf GitHub eine neuere VERSION liegt, und legt auf Wunsch
    eine Auslösedatei mit einem festen Stichwort (install / rollback) ab. Laden, Prüfen und Einspielen macht der
    getrennte Root-Helfer pipbox-swupdate.py.
    """
    CHECK_EVERY = 6 * 3600     # Sekunden zwischen zwei Abfragen bei GitHub (Abfrage von selbst im Hintergrund)
    FRESH_AFTER = 5 * 60       # wer die Seite oder die Karte öffnet, bekommt eine Antwort, die nicht älter als so viele Sekunden ist
    RETRY_AFTER_ERROR = 30 * 60   # ein Fehlversuch (kein Internet) wird früher wiederholt
    EARLY_SECONDS = 30 * 60       # in der ersten halben Stunde nach dem Start (Router und Mobilfunk brauchen oft einige Minuten) ...
    RETRY_EARLY = 3 * 60          # ... wird ein Fehlversuch schon nach 3 Minuten wiederholt
    RAW = "https://raw.githubusercontent.com/IRL4YOU/irl4you-pip/main/"
    API = "https://api.github.com/repos/IRL4YOU/irl4you-pip/releases?per_page=30"
    STATUS = "/run/pipbox-swupdate/status.json"
    BACKUP = "/var/lib/pipbox-backup"
    STAGE = "Beta"

    def __init__(self, state_dir, demo, send):
        self.req = os.path.join(state_dir, "swupdate-request")
        self.demo, self.send = demo, send
        self.lock = threading.Lock()
        self.cache, self.cache_t = None, 0.0
        self.started = time.time()
        self.rel_cache, self.rel_t = [], 0.0
        here = os.path.dirname(os.path.abspath(__file__))
        self.version = (read(os.path.join(here, "VERSION"), "") or "0.0.0").strip()
        self.fake = {}
        self.fake_t0 = 0.0

    CONTENTS = "https://api.github.com/repos/IRL4YOU/irl4you-pip/contents/%s?ref=main"

    def _get(self, name, limit):
        """Eine Datei des Repositorys lesen. Zuerst über die API von GitHub (immer der aktuelle Stand), dann über raw.githubusercontent.com: Dort liegt
        eine neue Version bis zu fünf Minuten im Zwischenspeicher, die Box zeigte kurz nach einer Veröffentlichung noch die vorherige Version als neueste."""
        last = None
        for url, extra in ((self.CONTENTS % name, {"Accept": "application/vnd.github.raw+json"}), (self.RAW + name, {})):
            try:
                req = urllib.request.Request(url, headers=dict(extra, **{"User-Agent": "irl4you-box"}))
                with urllib.request.urlopen(req, timeout=8) as r:
                    return r.read(limit + 1)[:limit].decode("utf-8", "replace")
            except OSError as e:                      # auch HTTP-Fehler (z. B. 403 bei erreichter Anfragegrenze): dann der nächste Weg
                last = e
        raise last

    @staticmethod
    def _first_section(text):
        lines, out, started = text.splitlines(), [], False
        for l in lines:
            if l.startswith("## "):
                if started:
                    break
                started = True
            if started:
                out.append(l.rstrip())
            if len(out) >= 25:
                break
        return "\n".join(out)

    @classmethod
    def _sections_since(cls, text, current, max_sections=6, max_lines=160):
        """Die Änderungen aller Versionen, die neuer sind als die installierte (neueste zuerst), aus dem Text der CHANGELOG.md. Wer mehrere
        Versionen übersprungen hat, sieht so alles, was neu ist. Ist keine neuer (oder die Überschriften sind unbekannt), gilt der erste Abschnitt."""
        def num(v):
            m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v or "")
            return tuple(int(x) for x in m.groups()) if m else None
        cur = num(current)
        out, keep, sections = [], False, 0
        for l in text.splitlines():
            if l.startswith("## "):
                m = re.match(r"^##\s+(\d+\.\d+\.\d+)", l)
                v = num(m.group(1)) if m else None
                keep = v is not None and cur is not None and v > cur
                if keep:
                    sections += 1
                    if sections > max_sections:
                        break
            if keep:
                out.append(l.rstrip())
                if len(out) >= max_lines:
                    break
        return "\n".join(out) if out else cls._first_section(text)

    def check(self, force=False, fresh=False):
        """Fragt die neueste Version auf GitHub ab (im Hintergrund höchstens alle 6 Stunden und nie während einer Übertragung, außer force). fresh: wer
        die Seite oder die Karte öffnet, will wissen, ob es gerade eine neue Version gibt: Eine Antwort, die älter als fünf Minuten ist, wird erneuert
        (vorher blieb eine neue Version bis zu sechs Stunden unsichtbar, wenn die Box kurz davor nachgefragt hatte)."""
        with self.lock:
            early = time.time() - self.started < self.EARLY_SECONDS
            wait = (self.RETRY_EARLY if early else self.RETRY_AFTER_ERROR) if self.cache and self.cache.get("error") else self.CHECK_EVERY
            if self.cache and self.cache.get("stale"):
                wait = self.RETRY_EARLY                    # die Antwort war älter als die installierte Version: bald noch einmal fragen
            if fresh:
                wait = min(wait, self.FRESH_AFTER)
            if not force and self.cache and (time.time() - self.cache_t < wait or self.send._active()):
                return self.cache
            if not force and not self.cache and not self.demo and self.send._active():
                return {"checked_at": None}          # während der Übertragung nicht über das Mobilfunknetz nachfragen
        res = {"checked_at": int(time.time())}
        if self.demo:
            res.update(latest="0.9.1", notes="## 0.9.1 (Demo)\n- Beispiel für eine neue Version.")
        else:
            try:
                v = self._get("VERSION", 64).strip()
                if not VERSION_RE.match(v):
                    raise ValueError("ungültige Versionsnummer")
                res["latest"] = v
                if vkey(v) < vkey(self.version):
                    # Die Antwort von GitHub ist älter als die installierte Version (veralteter Zwischenspeicher, kurz nach einer Veröffentlichung): Die
                    # installierte Version ist dann die neueste, die wir kennen; nach wenigen Minuten wird noch einmal gefragt.
                    res["latest"], res["stale"] = self.version, True
                try:
                    res["notes"] = self._sections_since(self._get("CHANGELOG.md", 60000), self.version)
                except OSError:
                    res["notes"] = ""
            except (OSError, ValueError):
                res["error"] = "GitHub ist nicht erreichbar oder lieferte keine gültige Versionsnummer."
        with self.lock:
            self.cache, self.cache_t = res, time.time()
        return res

    def auto_loop(self):
        """Fragt von selbst nach (alle 6 Stunden, check() hält das ein und fragt nie während einer Übertragung), damit der Punkt
        "Oberfläche" in der Kopfleiste auch dann stimmt, wenn gerade niemand die Seite offen hat."""
        time.sleep(30)
        while True:
            try:
                self.check()
            except Exception as e:                # nie den Dienst beenden
                print("swupdate auto_check:", e)
            time.sleep(60 if time.time() - self.started < self.EARLY_SECONDS else 15 * 60)

    def releases(self, force=False):
        """Veröffentlichte Versionen (Releases) auf GitHub: [{version, date, notes}], höchstens alle 6 Stunden, nie während einer Übertragung."""
        if not force and (time.time() - self.rel_t < self.CHECK_EVERY or (not self.demo and self.send._active())):
            return self.rel_cache
        out = []
        if self.demo:
            out = [{"version": "0.9.1", "date": "2026-10-02", "notes": "Demo"}, {"version": "0.9.0", "date": "2026-10-01", "notes": "Demo"}]
        else:
            try:
                req = urllib.request.Request(self.API, headers={"User-Agent": "irl4you-box", "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read(400000))
                for x in data if isinstance(data, list) else []:
                    v = str(x.get("tag_name", "")).lstrip("v")
                    if VERSION_RE.match(v) and not x.get("draft") and not x.get("prerelease"):
                        out.append({"version": v, "date": str(x.get("published_at", ""))[:10],
                                    "notes": str(x.get("body") or "")[:300]})
            except (OSError, ValueError):
                out = self.rel_cache                                 # alte Liste behalten, wenn GitHub nicht antwortet
        with self.lock:
            self.rel_cache, self.rel_t = out, time.time()
        return out

    def backups(self):
        """Lokal gesicherte Versionen, neueste zuerst (die Verzeichnisse sind lesbar, enthalten nur Programmdateien)."""
        if self.demo:
            return ["0.9.0"]
        out = []
        try:
            for n in os.listdir(self.BACKUP):
                p = os.path.join(self.BACKUP, n)
                if VERSION_RE.match(n) and os.path.isdir(os.path.join(p, "opt-pipbox")):
                    out.append((os.stat(p).st_mtime, n))
            legacy = (read(os.path.join(self.BACKUP, "VERSION"), "") or "").strip()      # frühere Ablage (eine Version)
            if VERSION_RE.match(legacy) and os.path.isdir(os.path.join(self.BACKUP, "opt-pipbox")) \
                    and legacy not in [n for _, n in out]:
                out.append((os.stat(os.path.join(self.BACKUP, "VERSION")).st_mtime, legacy))
        except OSError:
            pass
        return [n for _, n in sorted(out, reverse=True)]

    def versions(self, force=False):
        """Alle wählbaren Versionen: gesichert und/oder auf GitHub, neueste Versionsnummer zuerst."""
        local = self.backups()
        rel = {r["version"]: r for r in self.releases(force)}
        res = []
        for v in sorted(set(local) | set(rel) | {self.version}, key=vkey, reverse=True):
            res.append({"version": v, "local": v in local, "github": v in rel, "current": v == self.version,
                        "date": rel.get(v, {}).get("date", ""), "notes": rel.get(v, {}).get("notes", ""),
                        "relation": "aktuell" if v == self.version else ("neuer" if vkey(v) > vkey(self.version) else "älter")})
        return res

    @staticmethod
    def _pct(st):
        p = st.get("progress")
        return p if isinstance(p, int) and not isinstance(p, bool) and 0 <= p <= 100 else 0

    def demo_fake(self):
        """Demo: ein vorgetäuschtes Update, dessen Fortschritt über einige Sekunden läuft (für die Ansicht ohne Box)."""
        t0 = self.fake_t0
        steps = ((0, 2, "Lade die neue Version von GitHub"), (2, 22, "Prüfe das Archiv"), (3, 30, "Sichere die jetzige Version"),
                 (4, 36, "Installiere Version %s" % "9.9.9"), (6, 58, "Kopiere Programme und Oberfläche"), (8, 78, "Prüfe den SRTLA-Sender (wird neu gebaut, wenn er sich geändert hat)"),
                 (10, 95, "Starte die Dienste neu"))
        age = time.time() - t0
        if age >= 12:
            return {"state": "done", "message": "Demo: Update vorgetäuscht.", "time": int(time.time()), "progress": 100}
        cur = [x for x in steps if x[0] <= age][-1]
        return {"state": "installing", "step": cur[2], "progress": cur[1], "time": int(time.time())}

    def progress_public(self):
        """Nur Zustand, Schritt und Prozent des Updates, ohne Anmeldung abrufbar: Beim Update startet die Oberfläche neu und kennt die Anmeldung des
        Browsers danach evtl. nicht mehr; die Seite zeigt den Fortschritt trotzdem weiter und lädt erst nach dem Ende neu. Keine Version, keine Adressen."""
        if self.demo:
            st = self.demo_fake() if self.fake_t0 else {}
        else:
            try:
                with open(self.STATUS) as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {}
        state = st.get("state") if st.get("state") in ("installing", "done", "rolledback", "failed", "refused") else "idle"
        if state != "installing" and (not st.get("time") or time.time() - st["time"] > 6 * 3600):
            state = "idle"
        step = st.get("step", "")
        return {"state": state, "step": step if state == "installing" and isinstance(step, str) else "", "progress": self._pct(st)}

    def status(self, force=False, fresh=False):
        chk = self.check(force, fresh)
        if self.demo:
            st = (self.demo_fake() if self.fake_t0 else dict(self.fake)) or {}
        else:
            try:
                with open(self.STATUS) as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {}
        latest = chk.get("latest")
        out = {"current": self.version, "stage": self.STAGE, "latest": latest, "notes": chk.get("notes", ""),
               "error": chk.get("error", ""), "checked_at": chk.get("checked_at"),
               "newer": bool(latest and vkey(latest) > vkey(self.version)),
               "state": st.get("state", "idle"), "step": st.get("step", ""), "message": st.get("message", ""),
               "frm": st.get("frm", ""), "to": st.get("to", ""), "progress": self._pct(st)}
        if st.get("state") in ("installing", "refused", "done", "rolledback", "failed") and st.get("time") \
                and st["state"] != "installing" and time.time() - st["time"] > 6 * 3600:
            out["state"], out["message"] = "idle", ""             # alte Meldung nach 6 Stunden ausblenden
        bk = [v for v in self.backups() if v != self.version]
        out["backup_version"] = bk[0] if bk else ""                  # Ziel des Knopfes "Zurück"
        out["versions"] = self.versions(force)
        out["sending"] = False if self.demo else bool(belacoder_running() or self.send._active())
        out["helper_installed"] = self.demo or os.path.exists("/etc/systemd/system/pipbox-swupdate.path")
        return out

    def request(self, action, confirm, version=None, older=False):
        if action not in ("install", "rollback", "switch"):
            raise ValueError("Unbekannte Aktion")
        if confirm is not True:
            raise ValueError("Bestätigung fehlt")
        st = self.status()
        if not st["helper_installed"]:
            raise ValueError("Der Update-Helfer ist nicht installiert (install.sh erneut ausführen)")
        if st["sending"]:
            raise ValueError("Es wird gerade gesendet. Bitte zuerst die Übertragung beenden.")
        if st["state"] == "installing":
            raise ValueError("Ein Update läuft bereits")
        if action == "install" and not st["newer"]:
            raise ValueError("Es gibt keine neuere Version")
        if action == "rollback" and not st["backup_version"]:
            raise ValueError("Es gibt keine gesicherte Version")
        if action == "switch":
            v = str(version or "").strip().lstrip("v")
            if not VERSION_RE.match(v):
                raise ValueError("Bitte eine Version wie 0.9.0 angeben")
            known = {x["version"]: x for x in st["versions"]}
            if v not in known or not (known[v]["local"] or known[v]["github"]):
                raise ValueError(f"Version {v} ist weder gesichert noch auf GitHub veröffentlicht")
            if v == self.version:
                raise ValueError(f"Version {v} ist schon installiert")
            if vkey(v) < vkey(self.version) and older is not True:
                raise ValueError("Das ist eine ältere Version. Bitte ausdrücklich bestätigen.")
            version = v
        if self.demo:
            self.fake_t0 = time.time()
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n" + (version + "\n" if action == "switch" else ""))


class Updates:
    """Systemupdates über den getrennten Root-Helfer (pipbox-update.py).

    Dieser Dienst hat keine Root-Rechte. Er legt nur eine Auslösedatei mit einem
    Stichwort aus fester Liste ab; der Helfer führt dann die festen Aktionen aus.
    """
    MODES = ("check", "dry", "run", "reboot")

    def __init__(self, state_dir, demo):
        self.demo = demo
        self.req = os.path.join(state_dir, "update-request")
        self.status_path = os.path.join(state_dir, "update-status.json")
        self.log_path = os.path.join(state_dir, "update.log")
        self.fake = {}
        self.lock = threading.Lock()
        self.last_try = 0.0              # wann zuletzt eine stille Suche angefordert wurde (Schleife und Anmeldung teilen sich das)

    def boot_id(self):
        return (read("/proc/sys/kernel/random/boot_id", "demo") or "demo").strip()

    def reboot_done(self, st):
        """Ist die Box seit dem Neustart-Befehl schon wieder gestartet? Erst die Boot-Kennung, sonst die Zeit."""
        rb = st.get("reboot_boot_id")
        if rb and rb != self.boot_id():
            return True
        try:
            up = float((read("/proc/uptime", "") or "0").split()[0])
            return bool(st.get("finished")) and st["finished"] < time.time() - up
        except (ValueError, IndexError, TypeError):
            return False

    def status(self):
        if self.demo:
            st = dict(self.fake) or {"state": "never"}
            versions = {"belabox-linux-rk3588": "20241120-2", "belaui": "20250518-5",
                        "belacoder": "20250518-1"}
            log = st.pop("log", "")
            kernel = "5.10.160-belabox (Demo)"
        else:
            try:
                with open(self.status_path) as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {"state": "never"}
            versions = installed_versions()
            log = ""
            try:
                with open(self.log_path) as f:
                    log = "".join(f.readlines()[-60:])
            except OSError:
                pass
            kernel = os.uname().release
        st["reboot_pending"] = bool(st.get("reboot_required")) and st.get("reboot_boot_id") == self.boot_id()
        if st.get("state") == "rebooting" and self.reboot_done(st):
            # Der Zustand "Neustart" stammt von vor dem letzten Start der Box und ist längst erledigt
            st["state"] = "done"
            st["message"] = "Die Box ist nach dem Neustart wieder oben."
        st["versions"] = versions
        st["kernel_running"] = kernel
        st["streaming"] = belacoder_running() if not self.demo else False
        st["helper_installed"] = self.demo or os.path.exists("/etc/systemd/system/pipbox-update.path")
        st["log"] = log
        return st

    def request(self, mode, confirm):
        if mode not in self.MODES:
            raise ValueError("Unbekannte Aktion")
        if mode in ("run", "reboot") and not confirm:
            raise ValueError("Bestätigung fehlt")
        cur = self.status()
        if cur.get("state") == "running":
            raise ValueError("Es läuft bereits eine Aktion")
        if mode in ("run", "reboot") and cur["streaming"]:
            raise ValueError("Übertragung läuft. Bitte zuerst den Stream beenden.")
        if not cur["helper_installed"]:
            raise ValueError("Update-Helfer ist nicht installiert")
        if self.demo:
            threading.Thread(target=self._fake, args=(mode,), daemon=True).start()
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(mode + "\n")

    AUTO_EVERY = 6 * 3600         # so oft sucht die Box von selbst nach Systemupdates (nie während einer Übertragung)
    AUTO_RETRY = 3600             # nach einer Suche ohne Ergebnis (kein Internet) erst nach einer Stunde wieder
    AUTO_AFTER_BOOT = 2 * 60      # nach dem Start der Box zwei Minuten abwarten (Dienste und Netz kommen hoch), dann suchen
    EARLY_UPTIME = 30 * 60        # in der ersten halben Stunde nach dem Start (Router und Mobilfunk brauchen oft einige Minuten) ...
    AUTO_RETRY_EARLY = 5 * 60     # ... wird ein Fehlversuch schon nach 5 Minuten wiederholt

    @classmethod
    def auto_check_due(cls, st, now, uptime, streaming, request_pending, last_try, helper=True):
        """Ist es Zeit für die stille Suche nach Systemupdates (alle 6 Stunden)? Reine Rechnung (testbar)."""
        if not helper or streaming or request_pending or uptime < cls.AUTO_AFTER_BOOT:
            return False
        if st.get("state") in ("running", "rebooting"):
            return False
        retry = cls.AUTO_RETRY_EARLY if uptime < cls.EARLY_UPTIME else cls.AUTO_RETRY
        if last_try and now - last_try < retry:
            return False
        last = st.get("last_check") or 0
        if last < now - uptime:
            return True                # seit dem Start der Box noch nicht gesucht: immer einmal nach dem Start
        return now - last >= cls.AUTO_EVERY

    def auto_check(self, now=None, last_try=None, present=False):
        """Legt, wenn es Zeit ist, die Anforderung "autocheck" ab. True, wenn angefordert wurde. Der Helfer sucht still: ohne Zustand
        "läuft", ohne Fehlermeldung bei fehlendem Internet; das Ergebnis erscheint wie bei der Suche per Knopf (gelber Punkt in der Kopfleiste).
        present: Jemand hat die Oberfläche geöffnet (Anmeldung): dann nicht erst AUTO_AFTER_BOOT nach dem Start abwarten."""
        if self.demo:
            return False
        try:
            uptime = float((read("/proc/uptime", "") or "0").split()[0])
        except (ValueError, IndexError):
            uptime = 0.0
        if present:
            uptime = max(uptime, float(self.AUTO_AFTER_BOOT))
        st = self.status()
        if last_try is None:
            last_try = self.last_try
        if not self.auto_check_due(st, now or time.time(), uptime, st.get("streaming", False), os.path.exists(self.req), last_try,
                                   helper=st.get("helper_installed", False)):
            return False
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("autocheck\n")
        self.last_try = time.time()
        return True

    def auto_loop(self):
        time.sleep(30)
        while True:
            try:
                self.auto_check()
            except Exception as e:                # nie den Dienst beenden
                print("auto_check:", e)
            # in der ersten halben Stunde nach dem Start jede Minute nachsehen (die Suche selbst ist erst nach AUTO_AFTER_BOOT fällig)
            try:
                early = float((read("/proc/uptime", "") or "0").split()[0]) < self.EARLY_UPTIME
            except (ValueError, IndexError):
                early = False
            time.sleep(60 if early else 15 * 60)

    def _fake(self, mode):
        f = self.fake
        f.update(state="running", mode=mode, message="")
        time.sleep(1.5)
        if mode == "run":
            total = 12
            for i in range(total + 1):
                f["progress"] = {"downloading": min(i * 2, total), "unpacking": max(0, i - 4),
                                 "setting_up": max(0, i - 8), "total": total}
                f["log"] = f"Get:{i} http://repo.example ...\nUnpacking paket-{i} ...\n"
                time.sleep(0.6)
            f.update(state="done", message="Update abgeschlossen.", reboot_required=True,
                     reboot_boot_id=self.boot_id(), available=0, belabox=[], packages=[])
        elif mode == "reboot":
            f.update(state="rebooting", message="Neustart wird eingeleitet. (Demo)")
        else:
            f.update(state="done", mode=mode, available=224, download="412 MB",
                     packages=["belaui", "belacoder", "belabox-linux-rk3588", "libc6"],
                     belabox=["belaui", "belacoder", "belabox-linux-rk3588"],
                     would_reboot=True, last_check=int(time.time()), held=[])


DJI_COMMANDS = ("state", "wifi_options", "scan", "add", "update", "use_saved", "delete_saved", "remove",
                "connect", "disconnect", "reconnect", "phone_pair", "phone_pair_stop", "phone_read", "phone_remove", "ctrl_scan", "ctrl_pair", "ctrl_remove")
DJI_FIELDS = ("addr", "name", "model", "kind", "wifi_ifname", "ssid", "password", "ip", "resolution", "fps", "bitrate",
              "stabilization", "autoconnect", "status_only")


class DjiService:
    """Dünne Schicht zum Bluetooth-Dienst (pipbox-dji, dji_daemon.py): leitet die Befehle der Oberfläche als JSON-Zeilen an
    127.0.0.1:9101 weiter (mit dem Token, das nur der Benutzer pipbox lesen kann), trägt neue Kameras in die Kameraliste ein
    und liefert den Zustand. Passwörter der Kamera-WLANs liefert der Dienst nie, hier kommen nur Namen an."""
    MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    HOST, PORT = "127.0.0.1", 9101

    # Ausgangswerte nach Rolle (nur für neue Kameras): das Hauptbild bekommt mehr, die kleinen Bilder weniger, weil die Box
    # sie ohnehin auf 480x270 verkleinert. Spart WLAN und Rechenzeit.
    PROFILES = {"main": {"resolution": "1080p", "fps": 30, "bitrate": 8000},
                "pip": {"resolution": "720p", "fps": 30, "bitrate": 4000}}

    def __init__(self, state_dir, cams, rtmp_app, rtmp_port, demo=False):
        self.config_path = os.path.join(state_dir, "dji-cameras.json")
        self.token_path = os.path.join(state_dir, "dji-token")
        self.cams, self.app, self.port = cams, rtmp_app, rtmp_port
        self.pipeline = None      # wird von main() gesetzt: Rolle der Kamera in der Pipeline
        self.demo = demo
        self.fake = {"cameras": {}, "scan": [], "scanning": False} if demo else None
        self._next_id = 1

    def role_for_key(self, key):
        """main / pip nach der gespeicherten Pipeline, sonst nach der Rolle in der Kameraliste."""
        cfg = getattr(self.pipeline, "cfg", None) or {}
        if cfg.get("main") == key:
            return "main"
        if key in (cfg.get("pip"), cfg.get("pip2"), cfg.get("pip3")):
            return "pip"
        cam = next((c for c in self.cams.cams if c["key"] == key), None) if self.cams else None
        return {"main": "main", "pip": "pip"}.get((cam or {}).get("role"))

    def _free_role(self):
        roles = {c["role"] for c in self.cams.cams}
        return "main" if "main" not in roles else "pip" if "pip" not in roles else "extra"

    def _call(self, req, timeout=8):
        """Eine Anfrage an den Bluetooth-Dienst (eine JSON-Zeile hin, die Antwort mit passender Nummer zurück)."""
        if self.demo:
            return self._fake_call(req)
        try:
            with open(self.token_path) as f:
                tok = f.read().strip()
        except OSError:
            raise RuntimeError("Der Bluetooth-Dienst (pipbox-dji) ist noch nicht bereit")
        rid = self._next_id = self._next_id + 1
        line = (json.dumps(dict(req, token=tok, id=rid)) + "\n").encode()
        try:
            with socket.create_connection((self.HOST, self.PORT), timeout=timeout) as sk:
                sk.settimeout(timeout)
                sk.sendall(line)
                buf = b""
                while True:
                    while b"\n" in buf:
                        one, buf = buf.split(b"\n", 1)
                        try:
                            resp = json.loads(one)
                        except ValueError:
                            continue
                        if isinstance(resp, dict) and resp.get("reply_to") == rid:
                            if resp.get("error"):
                                raise ValueError(str(resp["error"]))
                            return resp
                    chunk = sk.recv(65536)
                    if not chunk:
                        raise RuntimeError("Der Bluetooth-Dienst (pipbox-dji) hat nicht geantwortet")
                    buf += chunk
                    if len(buf) > 1 << 20:
                        raise RuntimeError("Antwort des Bluetooth-Dienstes zu groß")
        except OSError:
            raise RuntimeError("Der Bluetooth-Dienst (pipbox-dji) läuft nicht")

    def _config(self):
        try:
            with open(self.config_path) as f:
                d = json.load(f)
            return (d.get("cameras") or {}) if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def addr_for_key(self, key):
        """Geräteadresse der DJI-Kamera mit diesem Stream-Schlüssel (dji-xxxxxx) oder None. Nur-Akku-Kameras (Schlüssel des HDMI-Eingangs) zählen nicht."""
        if not (isinstance(key, str) and key.startswith("dji-")):
            return None
        for addr, cfg in self._config().items():
            if cfg.get("rtmp_key") == key and not cfg.get("status_only"):
                return addr
        return None

    def remove_for_key(self, key):
        """Die DJI-Kamera mit diesem Schlüssel aus dem Kamera-Dienst entfernen (für "Kamera entfernen" in der Kameraliste). True, wenn es sie gab."""
        addr = self.addr_for_key(key)
        if not addr:
            return False
        try:
            return not self._call({"cmd": "remove", "addr": addr}).get("error")
        except (RuntimeError, ValueError):
            return False

    def fps_for_key(self, key):
        """Eingestellte Bildrate einer DJI-Kamera (Schlüssel dji-xxxxxx), sonst None. Manche DJI-Modelle melden in ihren
        Stream-Metadaten keine Bildrate; dann zeigen wir den Wert, mit dem wir sie starten."""
        if not key.startswith("dji-"):
            return None
        for cfg in self._config().values():
            if isinstance(cfg, dict) and cfg.get("rtmp_key") == key:
                return cfg.get("fps") if cfg.get("fps") in (25, 30) else 30
        return None

    def host_for_key(self, key):
        """(Adresse der Box, Verbindung) für die Adresse dieser DJI-Kamera nach der Verbindung in ihrer Karte, sonst None
        (dann gilt die Hauptverbindung)."""
        if not str(key).startswith("dji-"):
            return None
        for cfg in self._config().values():
            if isinstance(cfg, dict) and cfg.get("rtmp_key") == key:
                w = cfg.get("wifi_ifname")
                if not w:
                    return None
                if w == "manual":
                    return (cfg["ip"], "manual") if cfg.get("ip") else None
                for o in iface_ips():
                    if o["iface"] == w:
                        return (o.get("cam_ip") or o["ip"], w)
                return None
        return None

    EXTRAS_TTL = 3.0

    def camera_extras(self):
        """{Schlüssel: {battery, battery_age, charging}} der DJI-Kameras aus dem Zustand des Dienstes (3 Sekunden zwischengespeichert,
        die Statusseite fragt oft). Ohne Dienst leer."""
        now = time.monotonic()
        hit = getattr(self, "_extras", None)
        if hit and now - hit[0] < self.EXTRAS_TTL:
            return hit[1]
        out = {}
        try:
            for c in self._call({"cmd": "state"}, timeout=2).get("cameras", []):
                if c.get("rtmp_key") and c.get("battery") is not None:
                    out[c["rtmp_key"]] = {"battery": c["battery"], "battery_age": c.get("battery_age"), "charging": c.get("charging")}
        except (RuntimeError, ValueError):
            pass
        self._extras = (now, out)
        return out

    def _ensure_listed(self, cameras):
        """Jede Kamera des Dienstes steht auch in der Kameraliste der Box (damit sie als Bildquelle gewählt werden kann)."""
        if not self.cams:
            return
        have = {c["key"] for c in self.cams.cams}
        for c in cameras:
            key = c.get("rtmp_key")
            if key and key not in have and key != HDMI_KEY:      # Nur-Akku-Kameras tragen den Schlüssel des HDMI-Eingangs: der legt seine Kamera selbst an
                try:
                    self.cams.add(c.get("name") or c.get("model") or "DJI-Kamera", key, self._free_role())
                    have.add(key)
                except ValueError:
                    pass

    def status(self):
        out = {"available": False, "reason": "", "cameras": [], "scan": [], "scanning": False, "scan_error": "",
               "wifi_options": [], "adapters": [], "adapter_problems": [], "driver": {}, "bleak": True,
               "phones": [], "phone_pairing": {}, "controllers": [], "ctrl_found": [], "ctrl_scan": {}, "ctrl_message": ""}
        try:
            out.update(self._call({"cmd": "state"}))
            out["wifi_options"] = self._call({"cmd": "wifi_options"}).get("wifi_options", [])
            ad = self._call({"cmd": "adapters"})
            out.update({k: ad[k] for k in ("adapters", "adapter_problems", "driver") if k in ad})
            out["available"] = True
            self._ensure_listed(out["cameras"])
        except RuntimeError as e:
            out["reason"] = str(e)
        except ValueError as e:
            out["reason"] = str(e)
        return out

    def command(self, d):
        """Ein Befehl der Oberfläche an den Dienst. Nur die bekannten Befehle und Felder gehen durch."""
        cmd = d.get("cmd")
        if cmd not in DJI_COMMANDS:
            raise ValueError("Unbekannter Befehl")
        req = {"cmd": cmd}
        for k in DJI_FIELDS:
            if k in d:
                req[k] = d[k]
        if cmd == "use_saved" or cmd == "delete_saved":
            req["ssid"] = d.get("ssid", "")
        if "addr" in req and not self.MAC_RE.match(str(req["addr"])):
            raise ValueError("Ungültige Geräteadresse")
        if cmd == "add":
            key = "dji-" + str(req.get("addr", "")).replace(":", "").lower()[-6:]
            role = self.role_for_key(key) or self._free_role()
            req["settings"] = dict(self.PROFILES.get(role, {}))      # erste Einstellung: Ausgangswerte nach Rolle
        if cmd == "update" and "name" in req and self.cams:
            # Der Name gilt auch in der Kameraliste der Box: dort umbenennen (ein doppelter Name wird hier abgelehnt)
            cfg = self._config().get(str(req.get("addr", "")).upper()) or {}
            cam = next((c for c in self.cams.cams if c["key"] == cfg.get("rtmp_key")), None)
            if cfg.get("status_only"):
                cam = None                                    # die Kamera der Liste gehört dem HDMI-Eingang, nicht umbenennen
            if cam and str(req["name"]).strip() and cam["name"] != str(req["name"]).strip()[:40]:
                self.cams.update(cam["id"], name=str(req["name"]))
        gone_key = None
        if cmd == "remove" and self.cams:
            cfg = self._config().get(str(req.get("addr", "")).upper()) or {}
            if not cfg.get("status_only"):
                gone_key = cfg.get("rtmp_key")
        old_key = None
        if cmd == "update" and req.get("status_only") is True and self.cams:
            old_key = (self._config().get(str(req.get("addr", "")).upper()) or {}).get("rtmp_key")
        res = self._call(req)
        if old_key and old_key.startswith("dji-") and not res.get("error"):
            # Der Akku erscheint nun bei der HDMI-Kamera: der bisherige eigene Eintrag der Kamera in der Kameraliste entfällt
            cam = next((c for c in self.cams.cams if c["key"] == old_key), None)
            if cam:
                self.cams.remove(cam["id"])
        if gone_key and not res.get("error") and self.cams:
            # "Entfernen" heißt entfernen: auch der Eintrag in der Kameraliste verschwindet, und ein noch sendender Stream kommt nicht von selbst zurück
            cam = next((c for c in self.cams.cams if c["key"] == gone_key), None)
            if cam:
                self.cams.remove(cam["id"])
            self.cams.forget(gone_key)
        if cmd == "add" and res.get("key") and self.cams:
            self._ensure_listed([{"rtmp_key": res["key"], "model": req.get("model"), "name": req.get("name")}])
        return {k: v for k, v in res.items() if k not in ("reply_to", "token")}

    # -- Vorschau (--demo): ein nachgestellter Dienst im Speicher, damit die Oberfläche ohne Bluetooth zu sehen ist
    def _fake_call(self, req):
        f, cmd = self.fake, req.get("cmd")
        if not f["cameras"] and not f.get("seeded"):
            f["seeded"] = True
            base = {"wifi_ifname": "eth1", "ssid": "", "ip": "192.168.80.1", "resolution": "1080p", "fps": 30, "bitrate": 8000,
                    "stabilization": "off", "autoconnect": True, "saved": ["KameraNetz"], "in_range": True, "retry_in": 0,
                    "publishing": False, "locked": False, "detail": "", "battery": None}
            f["cameras"]["D0:D0:4B:00:00:01"] = dict(base, addr="D0:D0:4B:00:00:01", name="Kamera vorn", model="Osmo Action 5 Pro",
                                                     kind="action5", rtmp_key="dji-000001", state="streaming", battery=82, charging=True, battery_age=3,
                                                     publishing=True, locked=True, detail="rtmp://192.168.80.1:1935/publish/dji-000001")
            f["cameras"]["F0:4F:E2:00:00:02"] = dict(base, addr="F0:4F:E2:00:00:02", name="Kamera hinten", model="Osmo Pocket 3",
                                                     kind="pocket3", rtmp_key="dji-000002", state="error", autoconnect=False,
                                                     resolution="720p", bitrate=4000, retry_in=0, battery=18, battery_age=95, charging=False,
                                                     detail="Kamera nicht gefunden. Ist sie an, Bluetooth aktiv und nicht mit dem Handy verbunden?")
        if cmd == "state":
            pair = f.get("pair_until", 0) - time.time()
            if f.get("pair_until") and pair <= 0 and not f.get("phones_seeded"):
                f["phones_seeded"] = True                 # Vorschau: nach der Kopplungszeit steht ein Handy in der Liste
                f.setdefault("phones", []).append({"addr": "7C:A1:77:00:00:09", "name": "Pixel von Marco", "percent": 60, "steps": 5,
                                                   "age": 0, "error": "", "reading": False})
            return {"cameras": list(f["cameras"].values()), "scan": f["scan"], "scanning": f["scanning"], "scan_error": "",
                    "bleak": True, "phones": f.get("phones", []),
                    "phone_pairing": {"active": pair > 0, "left": max(0, int(pair)), "message": ""},
                    "controllers": f.get("controllers", []),
                    "ctrl_scan": {"active": 0 < f.get("ctrl_scan_until", 0) - time.time(), "left": max(0, int(f.get("ctrl_scan_until", 0) - time.time())), "message": ""},
                    "ctrl_found": [] if f.get("ctrl_scan_until", 0) - time.time() > 0 or not f.get("ctrl_scan_until") else
                    [r for r in ({"addr": "E4:11:22:33:44:55", "name": "Mini Controller", "rssi": -48, "input": True, "paired": False},
                                 {"addr": "AA:BB:CC:00:11:22", "name": "Jabra Evolve2 85", "rssi": -70, "input": False, "paired": False})
                     if r["addr"] not in {c["addr"] for c in f.get("controllers", [])}],
                    "ctrl_message": ""}
        if cmd == "ctrl_scan":
            f["ctrl_scan_until"] = time.time() + 5          # Vorschau: kurze Suche, dann stehen zwei Geräte in der Liste
            return {"ok": True}
        if cmd == "ctrl_pair":
            f.setdefault("controllers", []).append({"addr": str(req.get("addr", "")).upper(), "name": "Mini Controller", "connected": True, "battery": 87, "pairing": False})
            return {"ok": True}
        if cmd == "ctrl_remove":
            f["controllers"] = [c for c in f.get("controllers", []) if c["addr"] != str(req.get("addr", "")).upper()]
            return {"ok": True}
        if cmd == "phone_pair":
            f["pair_until"] = time.time() + 6              # Vorschau: kurze Kopplungszeit, danach erscheint ein Handy
            return {"ok": True}
        if cmd == "phone_pair_stop":
            f["pair_until"] = 0
            return {"ok": True}
        if cmd in ("phone_read", "phone_remove"):
            ph = f.get("phones", [])
            hit = next((p for p in ph if p["addr"] == str(req.get("addr", "")).upper()), None)
            if hit is None:
                raise ValueError("Unbekanntes Handy")
            if cmd == "phone_remove":
                ph.remove(hit)
            else:
                hit.update(percent=max(0, hit["percent"] - 20) if hit["percent"] else 80, age=0, error="")
            return {"ok": True}
        if cmd == "wifi_options":
            return {"wifi_options": [
                {"ifname": "wlan0", "ssid": "Handy-Hotspot", "ip": "10.1.1.20", "type": "client", "secret_missing": False},
                {"ifname": "eth0", "ssid": "", "ip": "192.168.1.20", "type": "other", "secret_missing": False},
                {"ifname": "eth1", "ssid": "", "ip": "192.168.80.1", "type": "other", "secret_missing": False}]}
        if cmd == "adapters":
            return {"adapters": [{"usb_id": "0b05:190e", "name": "ASUS USB-BT500", "vendor": "Realtek", "address": "A0:AD:9F:00:00:00", "powered": True}],
                    "adapter_problems": [], "driver": {}}
        if cmd == "scan":
            f["scan"] = [{"addr": "AA:BB:CC:00:00:03", "name": "OsmoAction6", "model": "Osmo Action 6", "kind": "action6",
                          "rssi": -61, "paired": False}]
            return {"ok": True}
        addr = str(req.get("addr", "")).upper()
        if cmd == "add":
            f["cameras"][addr] = {"addr": addr, "name": req.get("name") or req.get("model") or addr, "model": req.get("model", ""),
                                  "kind": req.get("kind", ""), "wifi_ifname": "", "ssid": "", "ip": "", "saved": [],
                                  "rtmp_key": "dji-" + addr.replace(":", "").lower()[-6:], "autoconnect": False,
                                  "state": "idle", "detail": "", "battery": None, "in_range": True, "retry_in": 0,
                                  "publishing": False, "locked": False, **{**{"resolution": "1080p", "fps": 30, "bitrate": 6000,
                                                                           "stabilization": "off"}, **(req.get("settings") or {})}}
            return {"ok": True, "key": f["cameras"][addr]["rtmp_key"]}
        cam = f["cameras"].get(addr)
        if cam is None:
            raise ValueError("Unbekannte Kamera")
        if cmd == "update":
            for k in DJI_FIELDS:
                if k in req and k != "addr" and not (k == "password" and not req[k]):
                    cam[k] = req[k]
        elif cmd == "remove":
            del f["cameras"][addr]
        elif cmd in ("connect", "reconnect"):
            if cam.get("status_only"):
                cam.update(state="status", detail="Nur Akkustand per Bluetooth", locked=True, battery=64)
            else:
                cam.update(state="streaming", detail="Die Kamera streamt (Vorschau)", publishing=True, locked=True, battery=64)
        elif cmd == "disconnect":
            cam.update(state="idle", detail="", publishing=False, locked=False, battery=None)
        elif cmd == "delete_saved":
            cam["saved"] = [n for n in cam.get("saved", []) if n != req.get("ssid")]
        return {"ok": True}


HDMI_KEY = "hdmi"           # Schlüssel der HDMI-Kamera: der HDMI-Dienst speist das Bild des HDMI-Eingangs unter diesem Namen in den RTMP-Eingang der Box ein


class HdmiService:
    """Dünne Schicht zum HDMI-Dienst (pipbox-hdmi, hdmi_daemon.py): leitet die Befehle der Oberfläche als JSON-Zeilen an 127.0.0.1:9102
    weiter (mit dem Token, das nur der Benutzer pipbox lesen kann), trägt die Kamera "HDMI" in die Kameraliste ein und liefert den Zustand.
    Der Dienst selbst läuft als root (Hardware-Kodierer, HDMI-Eingang) und nimmt nur geprüfte Zahlen und feste Auswahlen entgegen.
    Im Demo-Modus gibt es keinen Dienst: ein Eingang mit Signal, der sich ein- und ausschalten lässt."""
    HOST, PORT = "127.0.0.1", 9102
    CAMERA_NAMES = ("HDMI", "HDMI-Eingang", "HDMI 2")
    USB_NAMES = ("USB-Kamera", "USB-Webcam", "USB 2")        # Name der Kamera, wenn die Quelle die USB-Webcam ist (der Schlüssel bleibt "hdmi")
    ALLOWED = ("enabled", "bitrate", "fps", "audio", "source", "usb_format")        # was die Oberfläche ändern darf (der Schlüssel ist fest)
    TTL = 1.5
    DOWN = "Der HDMI-Dienst läuft nicht (Software-Update oder install.sh ausführen)"

    def __init__(self, state_dir, cams, demo=False):
        self.token_path = os.path.join(state_dir, "hdmi-token")
        self.cams, self.demo = cams, demo
        self.lock = threading.Lock()
        self._next_id = 0
        self._hit = (0.0, None)
        self.fake = dict(hdmi_daemon.DEFAULTS) if demo else None

    def _call(self, req, timeout=4):
        """Eine Anfrage an den HDMI-Dienst (eine JSON-Zeile hin, die Antwort mit passender Nummer zurück)."""
        try:
            with open(self.token_path) as f:
                tok = f.read().strip()
        except OSError:
            raise RuntimeError(self.DOWN)
        with self.lock:
            rid = self._next_id = self._next_id + 1
        line = (json.dumps(dict(req, token=tok, id=rid)) + "\n").encode()
        try:
            with socket.create_connection((self.HOST, self.PORT), timeout=timeout) as sk:
                sk.settimeout(timeout)
                sk.sendall(line)
                buf = b""
                while True:
                    while b"\n" in buf:
                        one, buf = buf.split(b"\n", 1)
                        try:
                            resp = json.loads(one)
                        except ValueError:
                            continue
                        if isinstance(resp, dict) and resp.get("reply_to") == rid:
                            if resp.get("error"):
                                if resp["error"] == "kein Zugriff":
                                    raise RuntimeError(self.DOWN)
                                raise ValueError(str(resp["error"])[:120])
                            return resp
                    chunk = sk.recv(65536)
                    if not chunk:
                        raise RuntimeError(self.DOWN)
                    buf += chunk
                    if len(buf) > 1 << 20:
                        raise RuntimeError(self.DOWN)
        except OSError:
            raise RuntimeError(self.DOWN)

    def _fake_status(self):
        f = self.fake
        usb = f.get("source") == "usb"
        return {"ok": True, "available": True, "state": "streaming" if f["enabled"] else "off", "message": "", "settings": dict(f),
                "signal": ({"plugged": True, "locked": True, "width": 1280, "height": 720, "fps": 30.0, "interlaced": False, "format": "", "depth": 0} if usb else
                           {"plugged": True, "locked": True, "width": 1920, "height": 1080, "fps": 60.0, "interlaced": False, "format": "RGB", "depth": 8}),
                "usb": ({"present": True, "name": "Beispiel-Webcam", "node": "/dev/video2", "format": "MJPEG 1280x720@30", "audio": "plughw:CARD=Beispiel"} if usb else
                        {"present": False, "name": "", "node": "", "format": "", "audio": ""}),
                "signal_known": True, "publishing": bool(f["enabled"]), "restarts": 0}

    def _listed(self):
        return bool(self.cams) and any(c["key"] == HDMI_KEY for c in self.cams.cams)

    def ensure_listed(self, source="hdmi"):
        """Die Kamera (Schlüssel "hdmi", Name je nach Quelle "HDMI" oder "USB-Kamera") steht in der Kameraliste der Box (damit sie als Bildquelle gewählt werden kann)."""
        if not self.cams or self._listed():
            return
        roles = {c["role"] for c in self.cams.cams}
        role = "main" if "main" not in roles else "pip" if "pip" not in roles else "extra"
        taken = {c["name"].casefold() for c in self.cams.cams}
        names = self.USB_NAMES if source == "usb" else self.CAMERA_NAMES
        name = next((n for n in names if n.casefold() not in taken), "USB-Kamera 2" if source == "usb" else "HDMI-Kamera")
        try:
            self.cams.add(name, HDMI_KEY, role)
        except ValueError:
            pass                                           # z. B. der Schlüssel ist schon vergeben: dann steht die Kamera schon in der Liste

    def status(self):
        """Zustand für die Oberfläche (1,5 Sekunden zwischengespeichert, die Seite fragt oft). Ohne Dienst: service false mit Meldung."""
        now = time.monotonic()
        with self.lock:
            if self._hit[1] is not None and now - self._hit[0] < self.TTL:
                return dict(self._hit[1])
        out = {"service": False, "available": None, "state": "down", "message": self.DOWN, "settings": {k: hdmi_daemon.DEFAULTS[k] for k in self.ALLOWED}, "signal": {},
               "usb": {}, "signal_known": False, "publishing": None, "restarts": 0}
        try:
            resp = self._fake_status() if self.demo else self._call({"cmd": "status"})
            for k in ("available", "state", "message", "signal", "usb", "signal_known", "publishing", "restarts"):
                if k in resp:
                    out[k] = resp[k]
            st = resp.get("settings") if isinstance(resp.get("settings"), dict) else {}
            out["settings"] = {k: st.get(k, hdmi_daemon.DEFAULTS[k]) for k in self.ALLOWED}
            out["service"] = True
            if out["settings"]["enabled"]:
                self.ensure_listed(out["settings"].get("source", "hdmi"))
        except (RuntimeError, ValueError) as e:
            out["message"] = str(e) or self.DOWN
        out["listed"] = self._listed()
        with self.lock:
            self._hit = (now, out)
        return dict(out)

    def set(self, req):
        """Einstellungen übernehmen (enabled, bitrate in kbit/s, fps, audio). Wirft ValueError (kurze Meldung) oder RuntimeError (Dienst fehlt)."""
        if not isinstance(req, dict):
            raise ValueError("Ungültige Anfrage")
        settings = {k: req[k] for k in self.ALLOWED if k in req}
        hdmi_daemon.clean_settings(settings, self.fake if self.demo else None)       # dieselbe Prüfung wie im Dienst, vorab
        if self.demo:
            self.fake = hdmi_daemon.clean_settings(settings, self.fake)
        elif settings:
            self._call({"cmd": "set", "settings": settings})
        if settings.get("enabled") is True:
            self.ensure_listed(settings.get("source") or (self.fake or {}).get("source") or self._last_source())
        with self.lock:
            self._hit = (0.0, None)
        return self.status()

    def _last_source(self):
        """Quelle laut dem zuletzt gelesenen Zustand (für den Namen der Kamera beim Einschalten ohne neue Quelle)."""
        with self.lock:
            st = self._hit[1] or {}
        return (st.get("settings") or {}).get("source", "hdmi")

    def on_camera_removed(self):
        """Die Kamera "HDMI" wurde aus der Liste entfernt: die Einspeisung dazu ausschalten (sonst käme die Kamera von selbst wieder)."""
        try:
            self.set({"enabled": False})
        except (RuntimeError, ValueError):
            pass


class TwitchStore:
    """Einstellungen der Akku-Warnung im Twitch-Chat: <state>/twitch.json (Rechte 0600, atomar geschrieben).

    Zwei Angaben: der Kanal (das Twitch-Konto, auf dem gestreamt wird; der Bot tritt ihm bei) und das Bot-Konto (Name und OAuth-Token des Kontos,
    das die Nachricht schreibt: ein zweites Konto oder dasselbe wie der Kanal). Name und Kanal werden klein geschrieben gespeichert (so kennt sie IRC),
    der Token ohne das Vorsatzwort "oauth:". Der Token bleibt auf der Box: Er steht nie in einer Antwort der Oberfläche (public() kennt nur token_set),
    in Meldungen oder Protokollen und nicht in der Einstellungssicherung (SettingsTransfer). Alles wird streng geprüft, denn die Werte landen in
    IRC-Zeilen: keine Steuerzeichen, kein CR/LF. Intern merkt sich die Datei auch, für welche Kamera schon gewarnt wurde ("warned": Schlüssel -> Zeitstempel)."""
    NAME_RE = re.compile(r"[A-Za-z0-9_]{3,25}")
    TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,100}")
    DEFAULT_MESSAGE = "Akkustand niedrig, bitte Akku wechseln: {Kamera} ({Prozent} %)"
    DEFAULTS = {"enabled": False, "login": "", "token": "", "channel": "", "threshold": 10, "message": DEFAULT_MESSAGE, "only_live": True, "via_account": False}

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = dict(self.DEFAULTS)
        self.warned = {}
        self.account = None                      # TwitchLogin (Anmeldung per Geräte-Code), wenn vorhanden
        self.bot = None                          # TwitchBotLogin: zweites Konto, das nur die Akku-Meldung schreibt (Anmeldung per Geräte-Code), wenn vorhanden
        try:
            with open(path) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = None
        if isinstance(saved, dict):
            self._adopt(saved)

    # ---- Prüfung der einzelnen Angaben (leer ist bei Name, Kanal und Token erlaubt: dann fehlt die Angabe)
    @classmethod
    def _name(cls, v, error):
        if not isinstance(v, str):
            raise ValueError(error)
        v = v.strip()
        if v and not cls.NAME_RE.fullmatch(v):
            raise ValueError(error)
        return v.lower()

    @classmethod
    def _token(cls, v):
        if not isinstance(v, str):
            raise ValueError("Token: 20 bis 100 Zeichen, nur Buchstaben, Ziffern, _ und -")
        v = v.strip()
        if not v:
            return ""
        if v[:6].lower() == "oauth:":
            v = v[6:]
        if not cls.TOKEN_RE.fullmatch(v):
            raise ValueError("Token: 20 bis 100 Zeichen, nur Buchstaben, Ziffern, _ und -")
        return v

    @staticmethod
    def _threshold(v):
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 50:
            raise ValueError("Warnen bei: Zahl von 1 bis 50")
        return v

    @staticmethod
    def _message(v):
        if not isinstance(v, str) or not 1 <= len(v.strip()) <= 300:
            raise ValueError("Nachricht: 1 bis 300 Zeichen")
        v = v.strip()
        if any(not ch.isprintable() for ch in v):
            raise ValueError("Nachricht: keine Sonderzeichen oder Zeilenumbrüche")
        if v[0] in "/.":
            raise ValueError("Nachricht: darf nicht mit / oder . beginnen")      # "/…" und ".…" wären Chat-Befehle
        return v

    ERR_CHANNEL = "Kanal: 3 bis 25 Zeichen, nur Buchstaben, Ziffern und _"
    ERR_LOGIN = "Bot-Konto: 3 bis 25 Zeichen, nur Buchstaben, Ziffern und _"
    ERR_MISSING = "Bitte Kanal, Bot-Konto und Token eintragen"

    def _adopt(self, saved):
        """Gespeicherte Werte übernehmen; was nicht (mehr) zur Prüfung passt, bleibt beim Standard (die Datei kann von Hand verändert sein)."""
        for key, check in (("login", lambda v: self._name(v, self.ERR_LOGIN)), ("channel", lambda v: self._name(v, self.ERR_CHANNEL)), ("token", self._token),
                           ("threshold", self._threshold), ("message", self._message)):
            if key in saved:
                try:
                    self.data[key] = check(saved[key])
                except ValueError:
                    pass
        if isinstance(saved.get("only_live"), bool):
            self.data["only_live"] = saved["only_live"]
        d = self.data
        d["via_account"] = saved.get("via_account") is True        # eingeschaltet mit dem angemeldeten Twitch-Konto statt Bot-Konto und Token von Hand
        d["enabled"] = saved.get("enabled") is True and (bool(d["channel"] and d["login"] and d["token"]) or d["via_account"])
        w = saved.get("warned")
        if isinstance(w, dict):
            self.warned = {k: float(t) for k, t in w.items() if isinstance(k, str) and KEY_RE.match(k) and isinstance(t, (int, float)) and not isinstance(t, bool)
                           and math.isfinite(t) and t > 0}

    def _write(self, data, warned):
        """Atomar schreiben; die Rechte 0600 gelten schon, bevor der Token in die Datei kommt."""
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump(dict(data, warned=warned), f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def set(self, req):
        """Einstellungen übernehmen. Fehlende Felder behalten ihren Wert, ein leeres Token-Feld behält den gespeicherten Token. Wirft ValueError (kurze Meldung)."""
        if not isinstance(req, dict):
            raise ValueError("Ungültige Anfrage")
        with self.lock:
            new = dict(self.data)
            for key in ("enabled", "only_live"):
                if key in req:
                    if not isinstance(req[key], bool):
                        raise ValueError("ja oder nein")
                    new[key] = req[key]
            if "channel" in req:
                new["channel"] = self._name(req["channel"], self.ERR_CHANNEL)
            if "login" in req:
                new["login"] = self._name(req["login"], self.ERR_LOGIN)
            if "token" in req:
                new["token"] = self._token(req["token"]) or new["token"]
            if "threshold" in req:
                new["threshold"] = self._threshold(req["threshold"])
            if "message" in req:
                new["message"] = self._message(req["message"])
            acc = self.account.login() if self.account else ""
            bot = self.bot.login() if self.bot else ""
            manual = bool(new["channel"] and new["login"] and new["token"])
            if new["enabled"] and not (manual or acc or (bot and new["channel"])):
                raise ValueError(self.ERR_MISSING)
            new["via_account"] = bool(new["enabled"] and not manual)
            try:
                self._write(new, self.warned)
            except OSError:
                raise RuntimeError("Die Einstellungen konnten nicht gespeichert werden")
            self.data = new

    def public(self):
        """Was die Oberfläche sieht: alles außer dem Token (nur, ob einer gespeichert ist)."""
        with self.lock:
            d = self.data
            acc = self.account.login() if self.account else ""
            bot = self.bot.login() if self.bot else ""
            return {"enabled": d["enabled"], "channel": d["channel"] or acc, "login": d["login"], "token_set": bool(d["token"]), "threshold": d["threshold"],
                    "message": d["message"], "only_live": d["only_live"], "account": acc, "bot": bot}

    def settings(self):
        """Alle Einstellungen samt Token, nur für den Chat-Client und den Hintergrunddienst. Nie nach außen geben."""
        with self.lock:
            d = dict(self.data)
        acc = self.account.login() if self.account else ""
        if acc:                                                    # angemeldetes Twitch-Konto hat Vorrang vor dem von Hand eingetragenen Token
            tok = self.account.token()
            d["login"], d["token"] = acc, tok                      # ist der Zugang gerade nicht verfügbar (wird erneuert), bleibt er leer: nie still als Bot-Konto schreiben
            d["channel"] = d["channel"] or acc
        return d

    def channel_name(self):
        """Der Kanal des Chats: der eingetragene, sonst der des angemeldeten Hauptkontos (wer sich per Geräte-Code anmeldet, trägt nichts ein). Ohne Netzzugriff."""
        with self.lock:
            ch = self.data["channel"]
        return ch or (self.account.login() if self.account else "")

    def notify_settings(self):
        """Die Angaben für die Akku-Meldung: wie settings(), aber ein angemeldetes Bot-Konto schreibt statt des Hauptkontos. Nur der Hintergrunddienst nimmt diese;
        der Chat (Lesen, Senden, Moderation) bleibt beim Hauptkonto. Ist das Bot-Konto angemeldet, sein Zugang aber gerade nicht verfügbar (wird erneuert),
        bleibt der Token leer: nie still mit dem Hauptkonto schreiben."""
        d = self.settings()
        bot = self.bot.login() if self.bot else ""
        if bot:
            d["login"], d["token"] = bot, self.bot.token()
        elif self.bot and self.bot.state == "abgelaufen":
            d["token"] = ""                                         # Anmeldung des Bots abgelaufen: schweigen, bis neu angemeldet oder abgemeldet wird
        return d

    # ---- für welche Kamera schon gewarnt wurde
    def warned_keys(self):
        with self.lock:
            return list(self.warned)

    def is_warned(self, key):
        with self.lock:
            return key in self.warned

    def _flush(self):
        try:
            self._write(self.data, self.warned)
        except OSError:
            pass                       # der Speicher gilt bis zum Neustart des Dienstes; beim nächsten erfolgreichen Schreiben ist es nachgeholt

    def mark_warned(self, key, when):
        with self.lock:
            self.warned[key] = float(when)
            self._flush()

    def unwarn(self, key):
        with self.lock:
            if self.warned.pop(key, None) is not None:
                self._flush()


class _IrcLines:
    """Zeilen aus einem Socket lesen, mit Frist."""
    MAX = 16384

    def __init__(self, sock, clock):
        self.sock, self.clock, self.buf = sock, clock, b""

    def get(self, until):
        """Nächste Zeile ohne Zeilenende; None, wenn die Frist abläuft; EOFError, wenn die Gegenseite schließt (oder keine Zeile zu sehen ist: kein IRC)."""
        while True:
            i = self.buf.find(b"\n")
            if i >= 0:
                line, self.buf = self.buf[:i], self.buf[i + 1:]
                return line.rstrip(b"\r").decode("utf-8", "replace")
            if len(self.buf) > self.MAX:
                raise EOFError
            left = until - self.clock()
            if left <= 0:
                return None
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                raise EOFError
            self.buf += chunk


class TwitchChat:
    """Schreibt eine Nachricht in den Twitch-Chat (IRC über TLS, nur Standardbibliothek). Nur Schreiben: Befehle aus dem Chat werden nicht gelesen.

    Ablauf je Nachricht (eine eigene Verbindung): verbinden (höchstens 10 s) -> PASS und NICK -> auf die Begrüßung (001) oder eine Absage warten -> die Fähigkeit
    "twitch.tv/commands" anfordern (nur dann meldet Twitch eine Ablehnung als NOTICE) -> JOIN -> PRIVMSG -> bis zu 2 s auf NOTICE-Antworten warten -> QUIT.
    PING wird mit PONG beantwortet. Alles zusammen dauert höchstens 15 s (die Namensauflösung zählt nicht mit). Host, Port und TLS sind für Tests einstellbar.
    Die PASS-Zeile mit dem Token wird nirgends ausgegeben. Text, den Twitch schickt, wird vor dem Anzeigen gekürzt und von Steuerzeichen und dem Token befreit.
    Twitch bestätigt eine angenommene Nachricht nicht: Kommt innerhalb der Wartezeit kein NOTICE, gilt sie als gesendet."""
    HOST, PORT = "irc.chat.twitch.tv", 6697
    MAX_BYTES = 450                  # Twitch erlaubt 500 Zeichen, eine IRC-Zeile höchstens 512 Byte
    OK = "Gesendet"
    NO_CONNECT = "Keine Verbindung zu Twitch"
    NO_TLS = "Sichere Verbindung zu Twitch nicht möglich (Zertifikat oder Uhrzeit der Box prüfen)"
    LOGIN_FAILED = "Anmeldung fehlgeschlagen (Token ungültig oder ohne Recht zum Schreiben)"
    NO_ANSWER = "Keine Antwort von Twitch (Zeitüberschreitung)"
    CLOSED = "Twitch hat die Verbindung beendet"
    NOT_ALLOWED = "Der Bot darf im Kanal nicht schreiben (z. B. nur Follower)"
    LOGIN_NOTICE_RE = re.compile(r"authentication failed|login unsuccessful|improperly formatted", re.I)      # so meldet Twitch eine Absage vor der Begrüßung

    def __init__(self, host=None, port=None, tls=True, context=None, connect_timeout=10.0, total=15.0, notice_wait=2.0, clock=time.monotonic, paths=None):
        self.paths = paths or ChatPaths()
        self.host, self.port, self.tls, self.context = host or self.HOST, port or self.PORT, tls, context
        self.connect_timeout, self.total, self.notice_wait, self.clock = connect_timeout, total, notice_wait, clock

    @staticmethod
    def clean_text(text):
        """Der Text für PRIVMSG oder None: nur druckbare Zeichen (kein CR, LF, NUL), nicht mit / oder . am Anfang, höchstens MAX_BYTES Byte."""
        t = "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in text).strip()                  # geschütztes Leerzeichen und Ähnliches -> Leerzeichen
        bad = ("Cc", "Zl", "Zp", "Cs", "Co", "Cn")
        if not t or any(unicodedata.category(ch) in bad or ch in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" for ch in t) or t[0] in "/.":
            return None
        raw = t.encode("utf-8")
        return t if len(raw) <= TwitchChat.MAX_BYTES else raw[:TwitchChat.MAX_BYTES].decode("utf-8", "ignore").rstrip()

    @staticmethod
    def _parse(line):
        """(Befehl, Rest) einer Zeile vom Server; Tags (@…) und Absender (:…) davor werden übersprungen."""
        if line.startswith("@"):
            line = line.partition(" ")[2]
        if line.startswith(":"):
            line = line.partition(" ")[2]
        cmd, _, rest = line.partition(" ")
        return cmd.upper(), rest

    @staticmethod
    def _notice_text(rest):
        return rest[1:] if rest.startswith(":") else rest.partition(" :")[2]

    @staticmethod
    def _scrub(text, token):
        """Text von Twitch zum Anzeigen: ohne den Token, ohne Steuerzeichen, höchstens 120 Zeichen."""
        text = text.replace(token, "…")
        text = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
        return text[:120] + ("…" if len(text) > 120 else "")

    @staticmethod
    def _pong(sock, rest):
        arg = "".join(ch for ch in rest if ch.isprintable())[:100]
        sock.sendall(b"PONG " + arg.encode("utf-8") + b"\r\n")

    def send(self, login, token, channel, text):
        """Als `login` (mit dem Token des Kontos) `text` in den Chat von `channel` schreiben. Gibt (ok, kurze Meldung auf Deutsch) zurück und wirft nichts;
        der Text einer Ausnahme wird nie übernommen (er könnte Angaben enthalten)."""
        try:
            return self._send(login, token, channel, text)
        except Exception:
            return False, "unbekannter Fehler"

    def _send(self, login, token, channel, text):
        if not all(isinstance(v, str) for v in (login, token, channel, text)):
            return False, "Ungültige Anfrage"
        login, channel, body = login.lower(), channel.lower(), self.clean_text(text)
        if body is None or not (TwitchStore.NAME_RE.fullmatch(login) and TwitchStore.NAME_RE.fullmatch(channel) and TwitchStore.TOKEN_RE.fullmatch(token)):
            return False, "Ungültige Anfrage"                       # nichts, was eine Zeile umbrechen oder einen Befehl einschleusen könnte, geht auf die Leitung
        until = self.clock() + self.total
        try:
            sock, _ = self.paths.connect(self.host, self.port, timeout=max(0.1, min(self.connect_timeout, until - self.clock())))
        except OSError:
            return False, self.NO_CONNECT
        try:
            if self.tls:
                try:
                    sock = (self.context or ssl.create_default_context()).wrap_socket(sock, server_hostname=self.host)
                except ssl.SSLError:
                    return False, self.NO_TLS
                except OSError:
                    return False, self.NO_CONNECT
            return self._talk(sock, login, token, channel, body, until)
        except (EOFError, OSError):
            return False, self.CLOSED
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _talk(self, sock, login, token, channel, body, until):
        rd = _IrcLines(sock, self.clock)
        sock.settimeout(max(0.1, until - self.clock()))
        sock.sendall(b"PASS oauth:" + token.encode("ascii") + b"\r\nNICK " + login.encode("ascii") + b"\r\n")
        while True:                                                 # auf die Begrüßung warten
            line = rd.get(until)
            if line is None:
                return False, self.NO_ANSWER
            cmd, rest = self._parse(line)
            if cmd == "001":
                break
            if cmd == "PING":
                self._pong(sock, rest)
            elif cmd in ("464", "465") or (cmd == "NOTICE" and self.LOGIN_NOTICE_RE.search(self._notice_text(rest))):
                return False, self.LOGIN_FAILED
        chan = channel.encode("ascii")
        sock.settimeout(max(0.1, until - self.clock()))
        sock.sendall(b"CAP REQ :twitch.tv/commands\r\nJOIN #" + chan + b"\r\nPRIVMSG #" + chan + b" :" + body.encode("utf-8") + b"\r\n")
        notice, end = None, min(self.clock() + self.notice_wait, until)
        try:
            while notice is None:                                   # eine Ablehnung meldet Twitch als NOTICE
                line = rd.get(end)
                if line is None:
                    break
                cmd, rest = self._parse(line)
                if cmd == "PING":
                    self._pong(sock, rest)
                elif cmd == "NOTICE":
                    notice = self._scrub(self._notice_text(rest), token)
        except EOFError:
            pass                                                    # Twitch schließt nach einer Absage manchmal gleich
        try:
            sock.sendall(b"QUIT\r\n")
        except OSError:
            pass
        if notice is None:
            return True, self.OK
        return False, self.NOT_ALLOWED + (": " + notice if notice else "")


def _ascii_digits(v):
    """True nur für nicht leere Zeichenketten aus den Ziffern 0-9 (str.isdigit() lässt auch "²" und andere Ziffernzeichen zu, int() scheitert dann)."""
    return isinstance(v, str) and v.isascii() and v.isdigit()


class ChatPaths:
    """Wege ins Internet für den Chat (Anmeldung, Lesen, Schreiben, Moderation). Die Box hat je Sendeweg eine eigene Quelladresse mit eigener Route (Quell-Routing),
    wie sie auch srtla_send benutzt: Ein Socket, der an die Adresse eines Sendewegs gebunden wird, geht genau über diesen Weg hinaus. Der Chat nimmt die gewählten
    Sendewege der Reihe nach, den besten zuerst (nach der Güte in srtla-links.txt, sonst nach der Reihenfolge der Einstellung), und weicht bei Fehler oder
    Stille auf den nächsten aus; zuletzt kommt die normale Route. Ein Weg, der versagt hat, rückt für `hold` Sekunden ans Ende. Das ist Ausweichen, kein
    Bündeln: Eine TCP-Verbindung lässt sich nicht auf mehrere Wege verteilen, und der Chat braucht nur wenige Bytes. Ohne Quelle (Tests) gilt nur die normale Route."""

    def __init__(self, sources=None, clock=time.monotonic, links_file="/run/pipbox-send/srtla-links.txt"):
        self.sources, self.clock, self.links_file = sources, clock, links_file
        self.bad = {}
        self.lock = threading.Lock()

    def _quality(self):
        """{Adresse: Güte} aus den letzten Zeilen von srtla-links.txt (kleiner ist besser; Reserve-Wege gelten als schlechter)."""
        out = {}
        try:
            with open(self.links_file, errors="replace") as f:
                lines = f.read()[-6000:].splitlines()
        except OSError:
            return out
        for ln in lines:
            m = re.search(r"links: (\d+\.\d+\.\d+\.\d+) .*?guete=(-?\d+) (genutzt|reserve)", ln)
            if m:
                g = int(m.group(2))
                out[m.group(1)] = (9999 if g < 0 else g) + (0 if m.group(3) == "genutzt" else 100000)
        return out

    def order(self):
        try:
            srcs = [x for x in (self.sources() if self.sources else []) if isinstance(x, str) and x]
        except Exception:
            srcs = []
        q = self._quality()
        now = self.clock()
        with self.lock:
            good = [x for x in srcs if self.bad.get(x, 0) <= now]
            bad = [x for x in srcs if x not in good]
        good.sort(key=lambda x: q.get(x, 50000))                                  # stabil: gleiche Güte behält die Reihenfolge der Einstellung
        return good + [None] + bad

    def fail(self, src, hold=300.0):
        if src:
            with self.lock:
                self.bad[src] = self.clock() + hold

    def connect(self, host, port, timeout=6.0):
        """(Socket, Quelladresse) über den ersten Weg, der klappt; OSError, wenn keiner geht."""
        last = OSError()
        for src in self.order():
            try:
                return socket.create_connection((host, port), timeout=timeout, source_address=(src, 0) if src else None), src
            except socket.gaierror as e:                       # Namensauflösung: hängt nicht am Weg, also keinen Weg sperren und nicht jeden einzeln versuchen
                raise e
            except OSError as e:
                last = e
                self.fail(src, 120.0)
        raise last

    def request(self, method, url, data=None, headers=None, timeout=15.0, limit=200_000, once=False, prefer=False):
        """(Status, Antworttext als Bytes) einer HTTP(S)-Anfrage über den ersten Weg, der antwortet; (0, b"") ohne Verbindung.
        once=True für Anfragen, die sich nicht wiederholen lassen (Erneuern des Zugangs mit einmaligem Schlüssel): Nur ein Fehler beim Verbinden führt zum
        nächsten Weg; ist die Anfrage erst gesendet und die Antwort geht verloren, wird nicht ein zweites Mal gesendet (Ergebnis dann (-1, b"")).
        prefer: ein Weg (auch None = normale Route), der zuerst versucht wird, zum Beispiel der, über den gerade eine Verbindung steht."""
        import http.client
        import urllib.parse
        u = urllib.parse.urlsplit(url)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        target = u.path + ("?" + u.query if u.query else "")
        order = self.order()
        if prefer is not False and prefer in order:
            order.remove(prefer)
            order.insert(0, prefer)
        for src in order:
            conn = None
            try:
                conn = cls(u.hostname, u.port, timeout=timeout, source_address=(src, 0) if src else None)
                try:
                    conn.connect()
                except socket.gaierror:
                    return 0, b""                                  # Namensauflösung hängt nicht am Weg: keinen Weg sperren, nicht alle durchprobieren
                except (OSError, http.client.HTTPException, ValueError):
                    self.fail(src, 120.0)
                    continue
                try:
                    conn.request(method, target, body=data, headers=headers or {})
                    r = conn.getresponse()
                    status = r.status
                    try:
                        return status, r.read(limit)
                    except (OSError, http.client.HTTPException, ValueError):
                        self.fail(src, 120.0)
                        if once:
                            return status, b""                         # Antwortzeile da, Rumpf verloren: der Status gilt
                        raise
                except (OSError, http.client.HTTPException, ValueError):
                    self.fail(src, 120.0)
                    if once:
                        return -1, b""                                 # gesendet, aber keine Antwort: nicht noch einmal senden
            except (OSError, http.client.HTTPException, ValueError):
                self.fail(src, 120.0)
            finally:
                try:
                    if conn:
                        conn.close()
                except OSError:
                    pass
        return 0, b""


class TwitchLogin:
    """Anmeldung beim Twitch-Konto des Nutzers per Geräte-Code ("Device Code Grant Flow" von Twitch): Die Box zeigt einen Code und einen Link, der Nutzer meldet
    sich auf twitch.tv selbst an und bestätigt die Rechte; die Box holt sich danach die Zugangsdaten ab. Die Zugangsdaten des Nutzers sehen wir nie.

    Die Anwendung ("IRL4YOU BOX", Typ "Public") hat nur eine Client-ID, die nicht geheim ist; ein Client-Secret gibt es nicht und wird nicht gebraucht.
    Der Zugangsschlüssel gilt 4 Stunden und wird im Hintergrund erneuert (keep). Der Erneuerungsschlüssel ist nur einmal verwendbar: Jede Erneuerung liefert einen
    neuen, der sofort atomar gespeichert wird. Bei einer öffentlichen Anwendung verfällt er nach 30 Tagen ohne Nutzung (dann: neu anmelden). Gespeichert wird in
    <state>/twitch-login.json (Rechte 0600). Tokens stehen nie in einer Antwort der Oberfläche, in Meldungen oder Protokollen. Im Vorschau-Modus (demo) läuft die
    Anmeldung ohne Netz ab. Adresse, Uhr und Wartezeit sind für Tests einstellbar."""
    CLIENT_ID = "a9w1hzb4d6nu70re8wxt6rpyxths35"
    ID_BASE = "https://id.twitch.tv/oauth2/"
    SCOPES = "chat:read chat:edit user:write:chat"                       # Lesen und Schreiben im Chat (user:write:chat: Senden über die Twitch-Schnittstelle mit Rückmeldung)
    MOD_SCOPES = "moderator:manage:banned_users moderator:manage:chat_messages"
    EVENT_SCOPES = "moderator:read:followers channel:read:redemptions"          # Follows und Kanalpunkte-Einlösungen (EventSub); nur, wenn der Nutzer die Ereignisse einschaltet
    SCOPES_MOD = "chat:read chat:edit user:write:chat moderator:manage:banned_users moderator:manage:chat_messages"      # nur, wenn der Nutzer die Moderation einschaltet
    GRANT = "urn:ietf:params:oauth:grant-type:device_code"
    REFRESH_BEFORE = 600                       # Sekunden vor dem Ablauf erneuern
    KEEP_EVERY = 30.0
    ERR_NET = "Keine Verbindung zu Twitch"
    ERR_DENIED = "Die Anmeldung wurde abgelehnt oder ist abgelaufen"

    def __init__(self, path, client_id=None, id_base=None, clock=time.time, sleep=time.sleep, demo=False, paths=None):
        self.paths = paths or ChatPaths()
        self.path, self.client_id = path, client_id or self.CLIENT_ID
        self.base = id_base or self.ID_BASE
        self.clock, self.sleep, self.demo = clock, sleep, demo
        self.lock = threading.RLock()
        self.tokens = None                     # {"access", "refresh", "expires_at", "login", "user_id", "scopes"}
        self.pending = None                    # {"device_code", "user_code", "uri", "expires_at", "interval"}
        self.state, self.error = "aus", ""
        self.gen = 0                           # zählt Anmeldeversuche: ein abgebrochener Versuch beendet seinen Abfrage-Faden
        try:
            with open(path) as f:
                saved = json.load(f)
            if isinstance(saved, dict) and isinstance(saved.get("access"), str) and isinstance(saved.get("refresh"), str):
                self.tokens = {"access": saved["access"], "refresh": saved["refresh"], "expires_at": float(saved.get("expires_at", 0)),
                               "login": str(saved.get("login", ""))[:25], "user_id": str(saved.get("user_id", ""))[:20],
                               "scopes": [str(x)[:60] for x in saved.get("scopes", [])][:30], "mod_off": saved.get("mod_off") is True}
                self.state = "angemeldet"
        except (OSError, ValueError, TypeError):
            pass

    # ---- Anfragen an Twitch (nur Standardbibliothek)
    def _call(self, url, fields=None, headers=None, once=False):
        """(Status, JSON-dict) einer Anfrage über die Wege des Chats; bei Netzfehlern (0, {})."""
        import urllib.parse
        data = urllib.parse.urlencode(fields).encode() if fields is not None else None
        hdr = dict(headers or {}, **{"User-Agent": "irl4you-box"})
        if data is not None:
            hdr["Content-Type"] = "application/x-www-form-urlencoded"
        status, body = self.paths.request("POST" if data is not None else "GET", url, data, hdr, once=once)
        try:
            out = json.loads(body.decode("utf-8", "replace") or "{}")
        except ValueError:
            out = {}
        return status, out if isinstance(out, dict) else {}

    def _write(self, tokens=None):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump(tokens if tokens is not None else self.tokens, f)
                f.flush()
                os.fsync(f.fileno())                          # auch bei Stromausfall nie eine halbe oder leere Datei
            os.replace(tmp, self.path)
            try:
                dfd = os.open(os.path.dirname(os.path.abspath(self.path)), os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    # ---- Zustand für die Oberfläche (ohne Tokens)
    def status(self):
        if self.demo:
            with self.lock:
                self._demo_step()
        t, p, state = self.tokens, self.pending, self.state                # nur lesen: nie hinter einer laufenden Anfrage an Twitch warten
        scopes = list((t or {}).get("scopes", []))
        has = "moderator:manage:banned_users" in scopes and "moderator:manage:chat_messages" in scopes
        ev = all(x in scopes for x in self.EVENT_SCOPES.split())
        out = {"state": state, "login": (t or {}).get("login", ""), "scopes": scopes, "error": self.error,
               "mod": has and not (t or {}).get("mod_off"),           # Moderation ist an: Rechte vorhanden und nicht von Hand ausgeschaltet
               "mod_scope": has,                                      # die Rechte hat der Zugang (Einschalten geht dann ohne neue Anmeldung bei Twitch)
               "events": ev,                                          # Rechte für Follows und Kanalpunkte sind da
               "helix_chat": "user:write:chat" in scopes}             # Senden über die Twitch-Schnittstelle ist möglich
        if p and state == "wartet":
            out.update(code=p["user_code"], uri=p["uri"], expires_in=max(0, int(p["expires_at"] - self.clock())))
        return out

    # ready/login/user_id nehmen die Sperre nicht: Sie lesen nur, und die Oberfläche (alle paar Sekunden) darf nie hinter einer Anfrage an Twitch warten.
    # Maßgeblich ist, ob Zugangsdaten da sind, nicht der Zustand einer gerade laufenden neuen Anmeldung (wartet/fehler).
    def ready(self):
        return bool(self.tokens)

    def login(self):
        t = self.tokens
        return t.get("login", "") if t else ""

    def user_id(self):
        t = self.tokens
        return t.get("user_id", "") if t else ""

    def token(self):
        """Gültiger Zugangsschlüssel oder "" (erneuert bei Bedarf sofort)."""
        t = self.tokens
        if not t:
            return ""
        if t["expires_at"] - self.clock() < 60:
            with self.lock:
                if self.tokens and self.tokens["expires_at"] - self.clock() < 60:
                    self.refresh()
            t = self.tokens
        if t and t["expires_at"] - self.clock() > 0:
            return t["access"]
        return ""

    # ---- Anmelden
    def scopes_for(self, mod=False, events=False):
        """Die Rechte für eine neue Anmeldung: immer die Grundrechte, Moderation und Ereignisse nur auf Wunsch, und was der Zugang schon hat, bleibt erhalten
        (wer die Ereignisse einschaltet, verliert die Moderation nicht, und umgekehrt)."""
        want = self.SCOPES.split()
        have = set(((self.tokens or {}).get("scopes")) or [])
        for flag, group in ((mod, self.MOD_SCOPES), (events, self.EVENT_SCOPES)):
            for sc in group.split():
                if (flag or sc in have) and sc not in want:
                    want.append(sc)
        return " ".join(want)

    def start(self, mod=False, events=False):
        scopes = self.scopes_for(mod, events)
        with self.lock:
            if self.state == "wartet" and self.pending and self.pending["expires_at"] > self.clock() and self.pending.get("scopes") == scopes:
                return self.status()
            self.gen += 1
            gen = self.gen
            self.error = ""
            if self.demo:
                self.pending = {"device_code": "demo", "user_code": "ABCD-EFGH", "uri": "https://www.twitch.tv/activate?device-code=ABCDEFGH",
                                "expires_at": self.clock() + 1800, "interval": 5, "since": self.clock(), "scopes": scopes}
                self.state = "wartet"
                return self.status()
            st, d = self._call(self.base + "device", {"client_id": self.client_id, "scopes": scopes})
            if st != 200 or not all(isinstance(d.get(k), str) for k in ("device_code", "user_code", "verification_uri")):
                self.state, self.error = ("angemeldet" if self.tokens else "fehler", self.ERR_NET if st == 0 else "Twitch hat die Anmeldung nicht angenommen")
                raise ValueError(self.error)
            try:
                interval = max(2, min(30, int(d.get("interval", 5))))
                expires = max(60, min(3600, int(d.get("expires_in", 1800))))
            except (TypeError, ValueError):
                interval, expires = 5, 1800
            uri = d["verification_uri"][:300]
            if not re.match(r"https://(www\.|id\.)?twitch\.tv/", uri):
                uri = "https://www.twitch.tv/activate"                              # nur Twitch-Adressen als Link und QR-Code
            self.pending = {"device_code": d["device_code"], "user_code": d["user_code"][:20], "uri": uri,
                            "expires_at": self.clock() + expires, "interval": interval, "scopes": scopes}
            self.state = "wartet"
        threading.Thread(target=self._poll, args=(gen,), daemon=True).start()
        return self.status()

    def cancel(self):
        with self.lock:
            self.gen += 1
            self.pending = None
            if self.state == "wartet":
                self.state = "angemeldet" if self.tokens else "aus"
            return self.status()

    def _poll(self, gen):
        while True:
            with self.lock:
                p = self.pending
                if gen != self.gen or not p or self.state != "wartet":
                    return
                if p["expires_at"] <= self.clock():
                    self.state, self.error, self.pending = ("angemeldet" if self.tokens else "fehler"), self.ERR_DENIED, None
                    return
                wait, device, scopes = p["interval"], p["device_code"], p.get("scopes", self.SCOPES)
            self.sleep(wait)
            st, d = self._call(self.base + "token", {"client_id": self.client_id, "scopes": scopes, "device_code": device, "grant_type": self.GRANT}, once=True)
            with self.lock:
                if gen != self.gen or self.state != "wartet":
                    if st == 200 and isinstance(d.get("access_token"), str):          # der Nutzer hat abgebrochen, Twitch hat trotzdem ausgestellt: Zugang gleich widerrufen
                        self._call(self.base + "revoke", {"client_id": self.client_id, "token": d["access_token"]})
                    return
                if st == 200 and isinstance(d.get("access_token"), str) and isinstance(d.get("refresh_token"), str):
                    self._adopt(d)
                    return
                msg = str(d.get("message", "")).lower()
                if st == 400 and "authorization_pending" in msg:
                    continue
                if st == 400 and "slow_down" in msg:
                    self.pending["interval"] = min(30, self.pending["interval"] + 5)
                    continue
                if st <= 0 or st >= 500:
                    continue                                    # Netz kurz weg oder Twitch hakt: weiter versuchen, bis der Code abläuft
                if st == 429:
                    self.pending["interval"] = min(30, self.pending["interval"] + 5)
                    continue
                self.state, self.error, self.pending = ("angemeldet" if self.tokens else "fehler"), self.ERR_DENIED, None
                return

    def _adopt(self, d):
        """Neue Zugangsdaten von Twitch übernehmen (mit Sperre aufgerufen): Konto prüfen, atomar speichern."""
        scopes = d.get("scope")
        scopes = [str(x)[:60] for x in scopes][:30] if isinstance(scopes, list) else self.SCOPES.split()
        try:
            expires = max(60.0, float(d.get("expires_in", 14400)))
        except (TypeError, ValueError):
            expires = 14400.0
        old = self.tokens or {}
        t = {"access": d["access_token"], "refresh": d["refresh_token"], "expires_at": self.clock() + expires, "scopes": scopes,
             "login": old.get("login", ""), "user_id": old.get("user_id", ""), "mod_off": old.get("mod_off") is True}
        werr = False
        try:
            self._write(t)                                           # sofort: der alte Erneuerungsschlüssel ist ab jetzt ungültig, der neue darf nie verloren gehen
        except OSError:
            werr = True
        st, v = self._call(self.base + "validate", None, {"Authorization": "OAuth " + t["access"]})
        changed = False
        if st == 200 and isinstance(v.get("login"), str):
            new = (v["login"][:25].lower(), str(v.get("user_id", ""))[:20], [str(x)[:60] for x in v["scopes"]][:30] if isinstance(v.get("scopes"), list) else scopes)
            changed = new != (t["login"], t["user_id"], t["scopes"])
            t["login"], t["user_id"], t["scopes"] = new
        self.tokens = t                                              # erst jetzt sichtbar: nie mit leerem Namen oder leerer Konto-Kennung
        self.pending, self.state, self.error = None, "angemeldet", ""
        if changed:
            try:
                self._write()
                werr = False
            except OSError:
                werr = True
        if werr:
            self.error = "Die Anmeldung konnte nicht gespeichert werden"

    # ---- Erneuern und Abmelden
    def refresh(self):
        """Zugangsschlüssel erneuern (mit Sperre). Netzfehler lassen alles, wie es ist; ein abgelehnter Erneuerungsschlüssel bedeutet: neu anmelden."""
        with self.lock:
            if not self.tokens:
                return False
            if self.clock() < getattr(self, "next_try", 0.0):
                return False                                          # nach Fehlschlägen nicht gleich wieder (Twitch oder Netz hakt)
            st, d = self._call(self.base + "token", {"client_id": self.client_id, "grant_type": "refresh_token", "refresh_token": self.tokens["refresh"]}, once=True)
            if st == 200 and isinstance(d.get("access_token"), str) and isinstance(d.get("refresh_token"), str):
                self.fails, self.next_try = 0, 0.0
                self._adopt(d)
                return True
            if st <= 0 or st == 429 or st >= 500:
                self.fails = getattr(self, "fails", 0) + 1
                self.next_try = self.clock() + min(300.0, 15.0 * 2 ** min(self.fails, 5))
                return False
            if st in (400, 401):
                keep = self.tokens.get("login", "")
                self.tokens, self.pending, self.state = None, None, "abgelaufen"
                self.error = keep
                try:
                    os.remove(self.path)
                except OSError:
                    pass
            return False

    def keep(self):
        """Hintergrunddienst: erneuert rechtzeitig vor dem Ablauf (läuft, solange die Box läuft, auch ohne Sendung)."""
        while True:
            try:
                if self.tokens and self.tokens["expires_at"] - self.clock() < self.REFRESH_BEFORE:
                    with self.lock:
                        if self.tokens and self.tokens["expires_at"] - self.clock() < self.REFRESH_BEFORE:
                            self.refresh()
            except Exception:
                pass                                                  # nie den Hintergrunddienst verlieren
            self.sleep(self.KEEP_EVERY)

    def set_mod(self, on):
        """Moderation von Hand aus- oder einschalten. Aus: Die Box nutzt die Rechte nicht mehr (Twitch erlaubt nicht, die Rechte eines Zugangs zu verkleinern;
        sie fallen erst bei Abmelden oder neuer Anmeldung ohne Moderation weg). Ein: nur, wenn der Zugang die Rechte hat, sonst ist eine neue Anmeldung nötig."""
        with self.lock:
            t = self.tokens
            if not t:
                raise ValueError("Zuerst mit Twitch anmelden")
            has = "moderator:manage:banned_users" in t.get("scopes", []) and "moderator:manage:chat_messages" in t.get("scopes", [])
            if on and not has:
                raise ValueError("Moderation braucht eine neue Anmeldung bei Twitch")
            t["mod_off"] = not on
            try:
                self._write()
            except OSError:
                pass
            return self.status()

    def refresh_now(self):
        """Sofort erneuern (auch gegen den Wartebetrieb nach Fehlschlägen), zum Beispiel nach einem 401 von Twitch. True, wenn es geklappt hat."""
        with self.lock:
            self.next_try = 0.0
            return bool(self.tokens) and self.refresh()

    def logout(self):
        with self.lock:
            self.gen += 1
            t = self.tokens
            self.tokens, self.pending, self.state, self.error = None, None, "aus", ""
            try:
                os.remove(self.path)
            except OSError:
                pass
        if t and not self.demo:
            self._call(self.base + "revoke", {"client_id": self.client_id, "token": t["access"]})      # Fehler egal: lokal ist die Anmeldung weg
        return self.status()

    # ---- Vorschau-Modus
    def _demo_step(self):
        if self.state == "wartet" and self.pending and self.clock() - self.pending.get("since", 0) > 6:
            self.tokens = {"access": "demo", "refresh": "demo", "expires_at": self.clock() + 14400, "login": "demo_streamer", "user_id": "1",
                           "scopes": (self.pending.get("scopes") or self.SCOPES).split()}
            self.pending, self.state = None, "angemeldet"


class TwitchBotLogin(TwitchLogin):
    """Zweites Twitch-Konto ("Bot"), das nur die Akku-Meldung in den Chat schreibt: dieselbe Anmeldung per Geräte-Code wie beim Hauptkonto, aber mit den kleinsten
    Rechten (Lesen und Schreiben im Chat über IRC, keine Moderation, keine Ereignisse) und eigener Datei <state>/twitch-bot-login.json. Der Chat der Oberfläche
    nutzt dieses Konto nie."""
    SCOPES = "chat:read chat:edit"
    MOD_SCOPES = EVENT_SCOPES = ""


class ThirdPartyEmotes:
    """Emotes von 7TV, BetterTTV und FrankerFaceZ (global und je Kanal) für den Chat. Die Box lädt die Listen selbst (über dieselben Wege wie der Chat),
    baut die Bildadressen **nur aus geprüften Kennungen** (nie aus Adressen der Dienste) und hält sie 30 Minuten. Der Reader ersetzt damit Wörter im Text
    durch [Adresse, von, bis]. Ohne Verbindung bleiben die Wörter Text; Fehler bleiben still."""
    TTL = 1800.0
    RETRY = 300.0
    MAX = 8000
    NAME_RE = re.compile(r"[A-Za-z0-9_:()!.\-]{1,40}")
    BTTV_ID, STV_ID, FFZ_ID = re.compile(r"[0-9a-f]{24}"), re.compile(r"[0-9A-Za-z]{26}"), re.compile(r"[0-9]{1,9}")
    URLS = {"bttv": "https://cdn.betterttv.net/emote/%s/2x.webp", "7tv": "https://cdn.7tv.app/emote/%s/2x.webp", "ffz": "https://cdn.frankerfacez.com/emote/%s/2"}
    HOSTS = re.compile(r"https://(cdn\.betterttv\.net|cdn\.7tv\.app|cdn\.frankerfacez\.com)/")

    def __init__(self, paths=None, fetch=None, clock=time.monotonic):
        self.paths, self.fetch, self.clock = paths, fetch or self._fetch, clock
        self.lock = threading.Lock()
        self.map, self.room, self.until, self.thread = {}, None, 0.0, None

    def _fetch(self, url):
        try:
            if self.paths is not None:
                status, body = self.paths.request("GET", url, headers={"User-Agent": "pipbox"}, timeout=10.0, limit=3_000_000)
            else:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "pipbox"}), timeout=10) as r:
                    status, body = r.status, r.read(3_000_000)
            return json.loads(body.decode("utf-8", "replace")) if status == 200 else (False if status in (404, 410) else None)
        except Exception:
            return None

    def snapshot(self):
        return self.map

    def ensure(self, room):
        room = room if isinstance(room, str) and re.fullmatch(r"[0-9]{1,12}", room) else ""
        with self.lock:
            if (self.thread is not None and self.thread.is_alive()) or (self.room == room and self.clock() < self.until):
                return
            self.room = room
            self.until = self.clock() + self.RETRY            # bis zur Fertigstellung nicht noch einmal starten
            self.thread = threading.Thread(target=self._load, args=(room,), daemon=True)
            self.thread.start()

    @classmethod
    def _add(cls, out, kind, items, idkey, namekey, idre):
        for it in (items or [])[:3000]:
            if len(out) >= cls.MAX or not isinstance(it, dict):
                continue
            i, n = it.get(idkey), it.get(namekey)
            if isinstance(i, (str, int)) and isinstance(n, str) and cls.NAME_RE.fullmatch(n) and idre.fullmatch(str(i)):
                out[n] = cls.URLS[kind] % i

    def _load(self, room):
        out, fails = {}, 0
        def get(url):
            nonlocal fails
            try:
                d = self.fetch(url)
            except Exception:
                d = None
            if d is None:
                fails += 1                                   # Zeitüberschreitung, 5xx, kaputte Antwort (ein 404 für den Kanal ist dagegen in Ordnung)
            return d or None
        ffz = lambda d: [e for st in ((d or {}).get("sets") or {}).values() if isinstance(st, dict) for e in (st.get("emoticons") or [])]
        def lst(x):
            return x if isinstance(x, list) else []
        def dct(x):
            return x if isinstance(x, dict) else {}
        steps = [
            lambda: self._add(out, "bttv", lst(get("https://api.betterttv.net/3/cached/emotes/global")), "id", "code", self.BTTV_ID),
            lambda: self._add(out, "ffz", ffz(get("https://api.frankerfacez.com/v1/set/global")), "id", "name", self.FFZ_ID),
            lambda: self._add(out, "7tv", lst(dct(get("https://7tv.io/v3/emote-sets/global")).get("emotes")), "id", "name", self.STV_ID)]
        if room:
            steps += [
                lambda: (lambda d: self._add(out, "bttv", lst(d.get("channelEmotes")) + lst(d.get("sharedEmotes")), "id", "code", self.BTTV_ID))(
                    dct(get("https://api.betterttv.net/3/cached/users/twitch/" + room))),
                lambda: self._add(out, "ffz", ffz(get("https://api.frankerfacez.com/v1/room/id/" + room)), "id", "name", self.FFZ_ID),
                lambda: self._add(out, "7tv", lst(dct(dct(get("https://7tv.io/v3/users/twitch/" + room)).get("emote_set")).get("emotes")), "id", "name", self.STV_ID)]
        for step in steps:
            try:
                step()
            except Exception:
                fails += 1
        with self.lock:
            if fails == 0 or not self.map:
                self.map = out                                # vollständig geladen (oder noch nichts da: Teilstand ist besser als nichts)
            else:
                merged = dict(self.map)                       # teilweise gescheitert: die gute alte Liste behalten und nur ergänzen
                merged.update(out)
                self.map = merged
            self.until = self.clock() + (self.TTL if fails == 0 else self.RETRY)

    def mark(self, item):
        """Wörter des Textes, die ein Emote sind, in item["emotes"] eintragen (mit den Twitch-eigenen zusammengeführt, nichts überlappt)."""
        m = self.map
        text = item.get("text") or ""
        if not m or not text:
            return
        found = [[m[w.group()], w.start(), w.end() - 1] for w in re.finditer(r"\S+", text) if w.group() in m]
        if not found:
            return
        merged = [list(e) for e in item.get("emotes") or []]
        for e in found:
            if all(e[2] < o[1] or e[1] > o[2] for o in merged):
                merged.append(e)
        item["emotes"] = sorted(merged, key=lambda e: e[1])[:60]


class TwitchReader:
    """Liest den Chat des eigenen Kanals für den Bereich "Chat" der Oberfläche (IRC über TLS, nur Standardbibliothek, nur Lesen).

    Gelesen wird **anonym** wie von einem Zuschauer (Name "justinfanNNNNN"): Dazu ist kein Token nötig, und der Token des Bot-Kontos kommt so nie in die Nähe
    dieser Verbindung. Der Kanal ist der aus der Twitch-Karte (TwitchStore). Die Verbindung steht nur, solange die Oberfläche den Chat abfragt (poll):
    Kommt IDLE Sekunden lang keine Abfrage, schließt der Hintergrundteil sie wieder (das schont Strom und Mobilfunk). Abbrüche werden mit wachsender
    Wartezeit wiederholt. Die letzten KEEP Nachrichten bleiben im Speicher; poll(since) liefert, was nach der Nummer `since` dazukam.
    Alles, was Twitch schickt, wird vor dem Speichern bereinigt (nur druckbare Zeichen, Längen begrenzt, Farben und Emote-Angaben streng geprüft);
    die Oberfläche setzt es als Text ein, nie als HTML. Im Vorschau-Modus (demo) entstehen Beispielnachrichten, ohne Netz."""
    HOST, PORT = "irc.chat.twitch.tv", 6697
    KEEP = 300
    IDLE = 120.0
    COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
    MID_RE = re.compile(r"[0-9a-fA-F-]{20,40}")
    BADGES = ("broadcaster", "moderator", "vip", "subscriber", "founder", "staff", "partner")        # nur diese Abzeichen zeigt die Oberfläche
    EMOTE_ID_RE = re.compile(r"[A-Za-z0-9_]{1,64}")                 # alte Kennungen sind Zahlen ("25"), neue (Kanal-, Abo-, Follower- und bewegte Emotes) "emotesv2_" plus 32 Zeichen = 41; bis 40 war zu knapp
    NOTICE_KINDS = {"sub": "sub", "resub": "sub", "subgift": "sub", "submysterygift": "sub", "giftpaidupgrade": "sub", "anongiftpaidupgrade": "sub",
                    "raid": "raid", "announcement": "notice", "ritual": "notice"}
    UNESC = {"s": " ", ":": ";", "\\": "\\", "r": "", "n": ""}

    STALE = 100.0                        # Sekunden ohne jede Zeile (auch ohne Antwort auf unser PING): Verbindung gilt als tot, nächster Weg
    PROBE = 40.0                         # nach so vielen stillen Sekunden schickt die Box selbst ein PING (Twitch antwortet mit PONG), sonst fiele ein toter Weg erst nach Minuten auf

    def __init__(self, store, demo=False, host=None, port=None, tls=True, context=None, clock=time.monotonic, wall=time.time, sleep=time.sleep, paths=None, third=None):
        self.paths = paths or ChatPaths()
        self.third = third or ThirdPartyEmotes(self.paths)
        self.events = None                  # TwitchEvents (EventSub), wird nach dem Start gesetzt
        self.store, self.demo = store, demo
        self.host, self.port, self.tls, self.context = host or self.HOST, port or self.PORT, tls, context
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self.lock = threading.Lock()
        self.items = collections.deque(maxlen=self.KEEP)
        self.next_id = 1
        self.gen = 0
        self.state = "aus"                  # aus | verbinde | ok | fehler
        self.channel = ""
        self.thread = None
        self.last_poll = 0.0
        self.demo_at = 0.0

    # ---- Zeilen von Twitch zerlegen
    @classmethod
    def _tags(cls, raw):
        out = {}
        for part in raw.split(";"):
            k, _, v = part.partition("=")
            if k and len(out) < 60:
                out[k] = re.sub(r"\\(.)", lambda m: cls.UNESC.get(m.group(1), m.group(1)), v)[:600]
        return out

    @staticmethod
    def _clean_map(text, limit):
        """Wie _clean (nicht druckbare Zeichen und Leerraum werden zu einem einzelnen Leerzeichen, Anfang und Ende ohne), aber mit Zuordnung:
        (bereinigter Text, Liste mit der neuen Stelle jedes ursprünglichen Zeichens oder -1)."""
        out, mp, pend = [], [-1] * len(text), False
        for i, ch in enumerate(text):
            if not ch.isprintable() or ch.isspace():
                pend = bool(out)
                continue
            if pend:
                out.append(" ")
                pend = False
            if len(out) < limit:
                mp[i] = len(out)
                out.append(ch)
        return "".join(out)[:limit], mp

    @staticmethod
    def _remap(emotes, mp):
        """Bereiche [id, von, bis] (Stellen im ursprünglichen Text) auf den bereinigten Text umrechnen; was wegfällt, entfällt."""
        out = []
        for eid, a, b in emotes:
            kept = [mp[i] for i in range(a, min(b, len(mp) - 1) + 1) if mp[i] >= 0]
            if kept:
                out.append([eid, kept[0], kept[-1]])
        return out

    @staticmethod
    def _clean(text, limit):
        t = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
        return t[:limit]

    @classmethod
    def _emotes(cls, raw, length):
        """"25:0-4,12-16/1902:6-10" -> [[id, von, bis], ...]; nur gültige Kennungen und Bereiche im Text."""
        out = []
        for chunk in raw.split("/")[:30]:
            eid, _, ranges = chunk.partition(":")
            if not cls.EMOTE_ID_RE.fullmatch(eid):
                continue
            for r in ranges.split(",")[:40]:
                a, _, b = r.partition("-")
                if _ascii_digits(a) and _ascii_digits(b) and int(a) <= int(b) < length:
                    out.append([eid, int(a), int(b)])
        return sorted(out, key=lambda e: e[1])

    def parse(self, line):
        """Eine IRC-Zeile -> Eintrag (dict ohne id/t) oder None. PING und anderes beachtet der Aufrufer."""
        tags = {}
        if line.startswith("@"):
            raw, _, line = line.partition(" ")
            tags = self._tags(raw[1:])
        prefix = ""
        if line.startswith(":"):
            prefix, _, line = line.partition(" ")
        cmd, _, rest = line.partition(" ")
        cmd = cmd.upper()
        if cmd == "CLEARMSG":                                         # eine Nachricht wurde gelöscht
            mid = tags.get("target-msg-id", "")
            return {"type": "del", "meta": True, "mid": mid, "text": "", "emotes": []} if self.MID_RE.fullmatch(mid) else None
        if cmd == "CLEARCHAT":                                        # Timeout, Bann oder der ganze Chat geleert
            uid = tags.get("target-user-id", "")
            dur = tags.get("ban-duration", "")
            return {"type": "clear", "meta": True, "uid": uid if _ascii_digits(uid) and len(uid) <= 20 else "",
                    "seconds": int(dur) if _ascii_digits(dur) and len(dur) <= 8 else 0, "text": "", "emotes": []}
        if cmd not in ("PRIVMSG", "USERNOTICE"):
            return None
        _, _, msg = rest.partition(" :")
        nick = prefix[1:].partition("!")[0]
        name = self._clean(tags.get("display-name") or nick, 40)
        color = tags.get("color", "")
        color = color if self.COLOR_RE.fullmatch(color) else ""
        kind, text = "msg", msg
        if cmd == "PRIVMSG" and text.startswith("\x01ACTION ") and text.endswith("\x01"):
            kind, text = "me", text[8:-1]
        native = self._emotes(tags.get("emotes", ""), len(text))            # Stellen beziehen sich auf den Text, wie Twitch ihn schickt
        text, mp = self._clean_map(text, 500)
        item = {"type": kind, "name": name, "color": color, "text": text, "emotes": self._remap(native, mp)}
        if cmd == "USERNOTICE":
            item["type"] = self.NOTICE_KINDS.get(tags.get("msg-id", ""), "notice")
            item["name"], item["emotes"] = "", []
            system = self._clean(tags.get("system-msg", ""), 300)
            item["text"] = (system + (" – " + text if text else "")) or text
            item["color"] = ""
        bits = tags.get("bits", "")
        if _ascii_digits(bits):
            item["bits"] = min(int(bits), 10_000_000)
        ev = self._event(cmd, tags, item)
        if ev is False:                                              # Einzelgeschenk einer Sammelaktion: die Sammelmeldung zeigt es schon
            return None
        if ev:
            item["ev"] = ev
        if cmd == "PRIVMSG":                                          # Kennungen der Nachricht und des Absenders (für Löschen, Timeout und Bann)
            if self.MID_RE.fullmatch(tags.get("id", "")):
                item["mid"] = tags["id"]
            uid = tags.get("user-id", "")
            if _ascii_digits(uid) and len(uid) <= 20:
                item["uid"] = uid
            item["login"] = self._clean(nick, 25).lower()
            names = [b.partition("/")[0] for b in tags.get("badges", "").split(",")[:20]]
            item["badges"] = [b for b in self.BADGES if b in names]
        if item["text"] and cmd == "PRIVMSG":                         # Emotes von 7TV, BTTV und FFZ (Liste des Kanals wird im Hintergrund geladen)
            self.third.ensure(tags.get("room-id", ""))
            self.third.mark(item)
        sent = tags.get("tmi-sent-ts", "")                           # Zeitpunkt, an dem Twitch die Nachricht angenommen hat (Millisekunden)
        if _ascii_digits(sent) and len(sent) <= 13:
            item["ts"] = int(sent) // 1000
        return item if item["text"] else None

    @staticmethod
    def _num(v, limit=10_000_000):
        return min(int(v), limit) if isinstance(v, str) and _ascii_digits(v) and len(v) <= 9 else 0

    @classmethod
    def _event(cls, cmd, tags, item):
        """Besondere Ereignisse für die auffällige Darstellung: {"k": Art, "n": Zahl} mit k = sub | gift | raid | cheer | announce.
        False = Einzelgeschenk einer Sammelaktion (wird nicht extra gezeigt)."""
        if cmd == "PRIVMSG":
            return {"k": "cheer", "n": item["bits"]} if item.get("bits") else None
        mid = tags.get("msg-id", "")
        if mid in ("sub", "resub"):
            return {"k": "sub", "n": cls._num(tags.get("msg-param-cumulative-months", ""), 1200)}
        if mid == "subgift":
            return False if tags.get("msg-param-community-gift-id") else {"k": "gift", "n": 1}
        if mid == "submysterygift":
            return {"k": "gift", "n": cls._num(tags.get("msg-param-mass-gift-count", ""), 1000) or 1}
        if mid in ("giftpaidupgrade", "anongiftpaidupgrade"):
            return {"k": "sub", "n": 0}
        if mid == "raid":
            return {"k": "raid", "n": cls._num(tags.get("msg-param-viewerCount", ""), 10_000_000)}
        if mid == "announcement":
            return {"k": "announce", "n": 0}
        return None

    def _add(self, item, gen=None):
        with self.lock:
            if gen is not None and gen != self.gen:
                return                                      # Nachzügler eines alten Fadens
            item["id"], item["t"] = self.next_id, int(item.get("ts") or self.wall())
            self.next_id += 1
            self.items.append(item)

    # ---- Abfrage durch die Oberfläche
    def poll(self, since=0):
        ch = ""
        if self.store:
            ch = self.store.channel_name() if hasattr(self.store, "channel_name") else self.store.data.get("channel", "")
        now = self.clock()
        with self.lock:
            self.last_poll = now
        if self.demo:
            self._demo(now)
            ch = ch or "demo"
            state = "ok"
        elif not ch:
            return {"state": "kein-kanal", "channel": "", "items": [], "last": 0}
        else:
            self._ensure(ch)
            state = self.state
            if self.events is not None:
                self.events.ensure()                        # Follows und Kanalpunkte (nur mit Rechten, nur eigener Kanal, nur solange abgefragt wird)
        with self.lock:
            last = self.next_id - 1
            if since > last:
                since = 0                           # die Box wurde neu gestartet: Verlauf von vorn
            items = [i for i in self.items if i["id"] > since]
        return {"state": state, "channel": ch, "items": items, "last": last}

    def _ensure(self, ch):
        with self.lock:
            alive = self.thread is not None and self.thread.is_alive() and self.channel == ch
            if alive:
                return
            if self.channel != ch:
                self.items.clear()
            self.channel = ch
            self.state = "verbinde"
            self.gen += 1                                   # jeder Start zählt: ein alter Faden (anderer Kanal, später wieder derselbe) beendet sich selbst
            gen = self.gen
            t = threading.Thread(target=self._run, args=(ch, gen), daemon=True)
            self.thread = t
        t.start()

    def _idle(self):
        return self.clock() - self.last_poll > self.IDLE

    def _mine(self, ch, gen):
        return self.gen == gen and self.channel == ch

    def _run(self, ch, gen):
        wait = 3.0
        while not self._idle() and self._mine(ch, gen):
            began = self.clock()
            try:
                self._session(ch, gen)
            except Exception:
                with self.lock:
                    if self._mine(ch, gen):
                        self.state = "fehler"
            if self.clock() - began > 30:
                wait = 3.0                                  # die Sitzung hat lange gehalten: schnell neu verbinden, nicht mit dem Wert vom letzten Ausfall
            end = self.clock() + wait
            while self.clock() < end and not self._idle() and self._mine(ch, gen):
                self.sleep(0.5)
            wait = min(wait * 2, 60.0)
        with self.lock:
            if self._mine(ch, gen):
                self.state = "aus"

    def _session(self, ch, gen):
        sock, src = self.paths.connect(self.host, self.port, timeout=8)
        ok = False
        try:
            if self.tls:
                sock = (self.context or ssl.create_default_context()).wrap_socket(sock, server_hostname=self.host)
            nick = "justinfan%05d" % random.randint(0, 99999)
            sock.sendall(("PASS SCHMOOPIIE\r\nNICK %s\r\n" % nick).encode("ascii"))
            rd = _IrcLines(sock, self.clock)
            until = self.clock() + 15
            while True:                                     # auf die Begrüßung warten
                line = rd.get(until)
                if line is None:
                    raise OSError
                cmd = line.partition(" ")[2].partition(" ")[0] if line.startswith(":") else line.partition(" ")[0]
                if cmd == "001":
                    break
                if cmd == "PING":
                    sock.sendall(b"PONG :tmi.twitch.tv\r\n")
            sock.sendall(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\nJOIN #" + ch.encode("ascii") + b"\r\n")
            ok = True
            with self.lock:
                if self._mine(ch, gen):
                    self.state = "ok"
            heard = probed = self.clock()
            while not self._idle() and self._mine(ch, gen):
                line = rd.get(self.clock() + 5)
                if line is None:
                    if self.clock() - heard > self.PROBE and self.clock() - probed > self.PROBE:
                        sock.sendall(b"PING :pipbox\r\n")
                        probed = self.clock()
                    if self.clock() - heard > self.STALE:
                        self.paths.fail(src, 300.0)                          # still geworden: diesen Weg meiden, über den nächsten neu verbinden
                        raise OSError("still")
                    continue
                heard = self.clock()
                if line.startswith("PING"):
                    sock.sendall(b"PONG :tmi.twitch.tv\r\n")
                    continue
                if TwitchChat._parse(line)[0] == "RECONNECT":            # nur der Befehl, nie ein Name in den Tags
                    return
                try:
                    item = self.parse(line)
                    if item:
                        self._add(item, gen)
                except Exception:
                    continue                                                   # eine unlesbare Zeile beendet nie die Sitzung
        except OSError:
            if not ok:
                self.paths.fail(src, 120.0)                                    # verbunden, aber TLS/Begrüßung hängt: diesen Weg eine Weile meiden
            raise
        finally:
            try:
                sock.close()
            except OSError:
                pass

    # ---- Vorschau-Modus: Beispielnachrichten
    DEMO = [("Anna_Streams", "#FF4500", "Hallo aus dem Chat! Das Bild ist heute richtig scharf"), ("kamerafreak", "#1E90FF", "Welche Kamera ist das gerade?"),
            ("Moderator_Max", "#2E8B57", "Bitte keine Werbung im Chat"), ("nightowl", "", "gg 🎉"), ("Lena", "#DAA520", "Der Ton ist super")]

    def _demo(self, now):
        if now - self.demo_at < 2.0 and self.items:
            return
        self.demo_at = now
        n = self.next_id
        if n % 11 == 0:
            self._add({"type": "sub", "name": "", "color": "", "text": "Anna_Streams hat den Kanal abonniert (Stufe 1)", "emotes": [], "ev": {"k": "sub", "n": 0}})
        elif n % 13 == 0:
            self._add({"type": "raid", "name": "", "color": "", "text": "Max_Raider schickt 42 Zuschauer zu dir", "emotes": [], "ev": {"k": "raid", "n": 42}})
        elif n % 17 == 0:
            self._add({"type": "msg", "name": "Lena", "color": "#DAA520", "text": "cheer500 Weiter so!", "emotes": [], "bits": 500, "ev": {"k": "cheer", "n": 500},
                       "mid": "%08x-0000-4000-8000-000000000000" % n, "uid": "102", "login": "lena", "badges": ["vip", "subscriber"]})
        elif n % 19 == 0:
            self._add({"type": "sub", "name": "", "color": "", "text": "nightowl verschenkt 5 Abos an die Community", "emotes": [], "ev": {"k": "gift", "n": 5}})
        elif n % 23 == 0:
            self._add({"type": "notice", "name": "", "color": "", "text": "Anna_Streams", "emotes": [], "ev": {"k": "follow", "n": 0}})
        elif n % 29 == 0:
            self._add({"type": "notice", "name": "", "color": "", "text": "Lena: Trink etwas Wasser", "emotes": [], "ev": {"k": "points", "n": 500}})
        elif n % 7 == 0 and (self.third.ensure("") or True) and self.third.snapshot():             # Vorschau: echte globale Emotes von 7TV/BTTV/FFZ
            names = sorted(self.third.snapshot())
            a, b = names[n % len(names)], names[(n * 7) % len(names)]
            item = {"type": "msg", "name": "emote_fan", "color": "#8A2BE2", "text": "Der Chat zeigt jetzt %s und %s" % (a, b), "emotes": [],
                    "mid": "%08x-0000-4000-8000-000000000000" % n, "uid": "103", "login": "emote_fan", "badges": []}
            self.third.mark(item)
            self._add(item)
        else:
            i = n % len(self.DEMO)
            name, color, text = self.DEMO[i]
            badges = {"Moderator_Max": ["moderator"], "Anna_Streams": ["subscriber"], "Lena": ["vip", "subscriber"]}.get(name, [])
            self._add({"type": "msg", "name": name, "color": color, "text": text, "emotes": [], "mid": "%08x-0000-4000-8000-000000000000" % n,
                       "uid": str(100 + i), "login": name.lower(), "badges": badges})


class TwitchMod:
    """Moderation im Chat des eigenen Kanals über die Twitch-Schnittstelle (Helix): Nachricht löschen, Timeout, Bann und Bann aufheben. Die Befehle "/ban",
    "/timeout" und "/unban" im IRC gibt es bei Twitch nicht mehr, darum läuft alles über die Schnittstelle mit dem Zugangsschlüssel des angemeldeten Kontos.

    Nötig sind die Rechte moderator:manage:banned_users und moderator:manage:chat_messages; der Nutzer bestätigt sie nur, wenn er die Moderation einschaltet
    (TwitchLogin.start(mod=True)). Das angemeldete Konto muss Streamer oder Moderator des Kanals sein. Namen werden über "Get Users" in Kennungen aufgelöst
    (zwischengespeichert). Fehler kommen als kurze deutsche Meldung (ValueError); Tokens stehen nie darin. Im Vorschau-Modus (demo) geschieht nichts im Netz."""
    API = "https://api.twitch.tv/helix/"
    MAX_SECONDS = 1209600                       # 14 Tage, das Höchste, was Twitch für einen Timeout erlaubt
    ERR_NET = "Keine Verbindung zu Twitch"
    ERR_LOGIN = "Zuerst mit Twitch anmelden"
    ERR_SCOPE = "Moderation ist noch nicht eingeschaltet"
    ERR_AUTH = "Anmeldung abgelaufen, bitte neu anmelden"
    ERR_FORBIDDEN = "Dazu fehlt die Berechtigung (Moderator im Kanal?)"
    ERR_OTHER = "Twitch hat die Anfrage nicht angenommen"
    ERR_UNSURE = "Verbindung gestört, unklar ob es angekommen ist"

    def __init__(self, store, account, api_base=None, client_id=None, demo_reader=None, demo=False, paths=None):
        self.store, self.account = store, account
        self.paths = paths or getattr(account, "paths", None) or ChatPaths()
        self.api = api_base or self.API
        self.client_id = client_id or TwitchLogin.CLIENT_ID
        self.demo, self.demo_reader = demo, demo_reader
        self.ids = {}
        self.lock = threading.Lock()

    def _call(self, method, path, params=None, body=None, once=False, timeout=15.0, token=None, prefer=False, accept=()):
        import urllib.error
        import urllib.parse
        import urllib.request
        tok = token or self.account.token()
        if not tok:
            raise ValueError(self.ERR_LOGIN)
        url = self.api + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        hdr = {"Authorization": "Bearer " + tok, "Client-Id": self.client_id, "User-Agent": "irl4you-box"}
        if data is not None:
            hdr["Content-Type"] = "application/json"
        status, raw = self.paths.request(method, url, data, hdr, timeout=timeout, once=once, prefer=prefer)
        if status == 401 and token is None and self.account.refresh_now():                  # Zugang war abgelaufen (zum Beispiel falsche Uhr): einmal erneuern, einmal wiederholen
            hdr["Authorization"] = "Bearer " + self.account.token()
            status, raw = self.paths.request(method, url, data, hdr, timeout=timeout, once=once, prefer=prefer)
        if status == 0:
            raise ValueError(self.ERR_NET)
        if status == -1:
            raise ValueError(self.ERR_UNSURE)
        try:
            out = json.loads(raw.decode("utf-8", "replace") or "{}")
        except ValueError:
            out = {}
        out = out if isinstance(out, dict) else {}
        if status in (200, 201, 202, 204) or status in accept:
            return out
        if status == 401:
            raise ValueError(self.ERR_AUTH)
        if status == 403:
            raise ValueError(self.ERR_FORBIDDEN)
        msg = TwitchReader._clean(str(out.get("message", "")), 120)
        raise ValueError(msg if status == 400 and msg else self.ERR_OTHER)

    def _context(self):
        """(Kanal-Kennung, Moderator-Kennung): der Kanal aus den Einstellungen, der Moderator ist das angemeldete Konto."""
        if not self.account.ready():
            raise ValueError(self.ERR_LOGIN)
        if not self.account.status().get("mod"):
            raise ValueError(self.ERR_SCOPE)
        me, mylogin = self.account.user_id(), self.account.login()
        ch = (self.store.settings().get("channel") or mylogin).lower()
        return (me if ch == mylogin else self.lookup(ch)), me

    def lookup(self, login):
        """Kennung eines Kontos aus dem Namen (Get Users)."""
        login = str(login).strip().lstrip("@").lower()
        if not TwitchStore.NAME_RE.fullmatch(login):
            raise ValueError("Ungültiger Name")
        with self.lock:
            if login in self.ids:
                return self.ids[login]
        d = self._call("GET", "users", {"login": login})
        rows = d.get("data") if isinstance(d.get("data"), list) else []
        if not rows or not isinstance(rows[0], dict) or not str(rows[0].get("id", "")).isdigit():
            raise ValueError("Dieses Konto gibt es nicht")
        with self.lock:
            if len(self.ids) > 200:
                self.ids.clear()
            self.ids[login] = str(rows[0]["id"])
            return self.ids[login]

    def do(self, req):
        """req: {"action": "delete"|"timeout"|"ban"|"unban", "message_id" | "user_id" | "user", "seconds", "reason"}."""
        if not isinstance(req, dict):
            raise ValueError("Ungültige Anfrage")
        act = req.get("action")
        if act not in ("delete", "timeout", "ban", "unban"):
            raise ValueError("Ungültige Anfrage")
        if self.demo:
            return self._demo(act, req)
        bc, me = self._context()
        base = {"broadcaster_id": bc, "moderator_id": me}
        if act == "delete":
            mid = req.get("message_id")
            if not isinstance(mid, str) or not TwitchReader.MID_RE.fullmatch(mid):
                raise ValueError("Ungültige Anfrage")
            self._call("DELETE", "moderation/chat", dict(base, message_id=mid))
            return {"ok": True, "action": act, "message": "Gelöscht"}
        uid = self._target(req)
        if act == "unban":
            self._call("DELETE", "moderation/bans", dict(base, user_id=uid))
            return {"ok": True, "action": act, "user_id": uid, "message": "Bann aufgehoben"}
        data = {"user_id": uid}
        reason = TwitchReader._clean(str(req.get("reason") or ""), 200)
        if reason:
            data["reason"] = reason
        if act == "timeout":
            try:
                sec = int(req.get("seconds", 600))
            except (TypeError, ValueError, OverflowError):
                raise ValueError("Dauer: Zahl in Sekunden")
            data["duration"] = max(1, min(self.MAX_SECONDS, sec))
        self._call("POST", "moderation/bans", base, {"data": data})
        return {"ok": True, "action": act, "user_id": uid, "message": "Timeout" if act == "timeout" else "Gebannt"}

    def _target(self, req):
        uid = req.get("user_id")
        if isinstance(uid, str) and _ascii_digits(uid) and len(uid) <= 20:
            return uid
        if isinstance(req.get("user"), str):
            return self.lookup(req["user"])
        raise ValueError("Ungültige Anfrage")

    def _demo(self, act, req):
        if act == "delete" and self.demo_reader is not None and isinstance(req.get("message_id"), str):
            self.demo_reader._add({"type": "del", "meta": True, "mid": req["message_id"], "text": "", "emotes": []})
        if act in ("timeout", "ban") and self.demo_reader is not None and _ascii_digits(str(req.get("user_id") or "")):
            self.demo_reader._add({"type": "clear", "meta": True, "uid": str(req["user_id"]), "seconds": int(req.get("seconds") or 0) if act == "timeout" else 0,
                                   "text": "", "emotes": []})
        return {"ok": True, "action": act, "user_id": str(req.get("user_id") or "1"),
                "message": {"delete": "Gelöscht", "timeout": "Timeout", "ban": "Gebannt", "unban": "Bann aufgehoben"}[act]}


class TwitchSender:
    """Nachrichten des Streamers aus der Oberfläche in den Chat (über das angemeldete Twitch-Konto, sonst Bot-Konto und Token von Hand). Höchstens eine
    Nachricht je Sekunde. Befehle mit "/" oder "." am Anfang werden nie als Text gesendet: "/ban Name [Grund]", "/timeout Name [Sekunden] [Grund]" und
    "/unban Name" gehen als Moderation über die Twitch-Schnittstelle (TwitchMod), alles andere wird abgelehnt."""
    COMMANDS = ("ban", "timeout", "unban")
    MIN_GAP = 1.0

    def __init__(self, store, chat=None, clock=time.monotonic, demo_reader=None, mod=None, helix=None):
        self.store, self.chat, self.clock, self.mod, self.helix = store, chat or TwitchChat(), clock, mod, helix
        self.demo_reader = demo_reader              # nur im Vorschau-Modus: die Nachricht erscheint im Beispiel-Chat, nichts geht ins Netz
        self.last = -1e9
        self.lock = threading.Lock()

    def _command(self, t):
        parts = t[1:].split()
        cmd = parts[0].lower() if t[0] == "/" and parts else ""
        if cmd not in self.COMMANDS or len(parts) < 2 or self.mod is None:
            raise ValueError("Befehle: /ban Name, /timeout Name [Sekunden], /unban Name")
        with self.lock:
            now = self.clock()
            if now - self.last < self.MIN_GAP:
                raise ValueError("Bitte kurz warten")
            self.last = now
        req = {"action": cmd, "user": parts[1]}
        rest = parts[2:]
        if cmd == "timeout" and rest and _ascii_digits(rest[0]):
            req["seconds"] = int(rest[0])
            rest = rest[1:]
        if cmd != "unban" and rest:
            req["reason"] = " ".join(rest)
        out = self.mod.do(req)
        return {"ok": True, "message": "%s: %s" % (out.get("message", ""), parts[1].lstrip("@"))}

    def say(self, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Nachricht fehlt")
        t = text.strip()
        if t[0] in "/.":
            return self._command(t)
        cfg = self.store.settings()
        if not (cfg["login"] and cfg["token"] and cfg["channel"]):
            raise ValueError("Zuerst mit Twitch anmelden")
        with self.lock:
            now = self.clock()
            if now - self.last < self.MIN_GAP:
                raise ValueError("Bitte kurz warten")
            self.last = now
        if self.demo_reader is not None:
            self.demo_reader._add({"type": "msg", "name": cfg["login"], "color": "#9146FF", "text": TwitchChat.clean_text(t) or "", "emotes": []})
            return {"ok": True, "message": TwitchChat.OK}
        t = TwitchChat.clean_text(t)
        if not t:
            raise ValueError("Nachricht ungültig")
        if self.helix is not None and self.helix.usable():             # über die Twitch-Schnittstelle: mit Rückmeldung, ob die Nachricht wirklich gesendet wurde
            return {"ok": True, "message": self.helix.send(cfg["channel"], t)}
        ok, msg = self.chat.send(cfg["login"], cfg["token"], cfg["channel"], t)
        if not ok:
            raise ValueError(msg)
        return {"ok": True, "message": msg}


class HelixChat:
    """Chat senden über die Twitch-Schnittstelle (POST chat/messages, Recht user:write:chat). Twitch antwortet auch dann mit 200, wenn die Nachricht verworfen wurde
    (Spam- oder Tempofilter, nur Abonnenten, langsamer Modus ...): darum zählt nur "is_sent", sonst steht der Grund von Twitch in der Meldung. Ohne das Recht
    (ältere Anmeldung) bleibt das Senden über IRC (TwitchChat), bis man sich neu anmeldet. Alle Aufrufe laufen über TwitchMod._call (Wege, Erneuern bei 401)."""
    ERR_DROP = "Twitch hat die Nachricht nicht gesendet"
    ERR_FORBIDDEN = "Twitch erlaubt das Senden hier gerade nicht (zum Beispiel nur für Abonnenten oder Follower)"

    def __init__(self, account, mod):
        self.account, self.mod = account, mod

    def usable(self):
        try:
            return bool(self.account.ready() and "user:write:chat" in (self.account.status().get("scopes") or []))
        except Exception:
            return False

    def send(self, channel, text):
        me = self.account.user_id()
        if not me:
            raise ValueError("Zuerst mit Twitch anmelden")
        ch = str(channel or "").strip().lstrip("#").lower()
        bid = me if (not ch or ch == self.account.login()) else self.mod.lookup(ch)
        try:
            d = self.mod._call("POST", "chat/messages", None, {"broadcaster_id": bid, "sender_id": me, "message": text}, once=True)
        except ValueError as e:
            if str(e) == self.mod.ERR_FORBIDDEN:
                raise ValueError(self.ERR_FORBIDDEN)
            raise                                                          # auch ERR_UNSURE (gesendet, Antwort verloren): "unklar, ob es angekommen ist"
        rows = d.get("data") if isinstance(d.get("data"), list) else []
        row = rows[0] if rows and isinstance(rows[0], dict) else {}
        if row.get("is_sent") is True:
            return TwitchChat.OK
        why = row.get("drop_reason") if isinstance(row.get("drop_reason"), dict) else {}
        msg = TwitchReader._clean(str(why.get("message") or ""), 120)
        raise ValueError(self.ERR_DROP + (": " + msg if msg else ""))


class WebSocketLite:
    """Kleiner WebSocket-Client nach RFC 6455 (nur Standardbibliothek) für EventSub: Aufbau mit Prüfung der Antwort, Text-Nachrichten (auch in Teilen), Ping wird mit
    Pong beantwortet, Schließen beendet. Keine Erweiterungen, der Server darf nicht maskieren, jede Nachricht höchstens MAX Byte. Zeitüberschreitungen verlieren
    keine halb gelesene Nachricht."""
    GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    MAX = 1 << 20

    def __init__(self, sock, clock=time.monotonic):
        self.sock, self.clock = sock, clock
        self.buf = bytearray()
        self.parts, self.first = bytearray(), None
        self.last_rx = clock()                                              # wann zuletzt Daten von der Gegenstelle kamen (auch Ping zählt)

    @classmethod
    def handshake(cls, sock, host, path, clock=time.monotonic, timeout=10.0):
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(b"GET " + path.encode("ascii") + b" HTTP/1.1\r\nHost: " + host.encode("ascii") + b"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: "
                     + key.encode("ascii") + b"\r\nSec-WebSocket-Version: 13\r\nUser-Agent: irl4you-box\r\n\r\n")
        buf, deadline = b"", clock() + timeout
        while b"\r\n\r\n" not in buf:
            left = deadline - clock()
            if left <= 0 or len(buf) > 16384:
                raise OSError("ws-handshake-timeout")
            sock.settimeout(left)
            chunk = sock.recv(4096)
            if not chunk:
                raise EOFError("ws-eof")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        if not re.match(r"HTTP/1\.[01] 101\b", lines[0]):
            raise OSError("ws-no-upgrade")
        hdr = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
        want = base64.b64encode(hashlib.sha1(key.encode("ascii") + cls.GUID).digest()).decode()
        if hdr.get("sec-websocket-accept") != want:
            raise OSError("ws-bad-accept")
        ws = cls(sock, clock)
        ws.buf += rest
        return ws

    @staticmethod
    def frame(opcode, payload=b""):
        mask, n = os.urandom(4), len(payload)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + n.to_bytes(2, "big") if n < 65536 else bytes([0x80 | 127]) + n.to_bytes(8, "big"))
        return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    def close(self):
        try:
            self.sock.sendall(self.frame(8, b"\x03\xe8"))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _need(self, n, deadline):
        while len(self.buf) < n:
            left = deadline - self.clock()
            if left <= 0:
                return False
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return False
            if not chunk:
                raise EOFError("ws-eof")
            self.last_rx = self.clock()
            self.buf += chunk
        return True

    def recv_message(self, timeout):
        """Die nächste Text-Nachricht oder None, wenn in `timeout` Sekunden keine vollständige da ist. EOFError: Gegenstelle hat geschlossen; OSError: Verstoß."""
        deadline = self.clock() + timeout
        while True:
            if self.clock() > deadline:
                return None                                              # auch ein Strom von Steuerrahmen hält die Schleife nicht fest
            if not self._need(2, deadline):
                return None
            b0, b1 = self.buf[0], self.buf[1]
            fin, op = b0 & 0x80, b0 & 0x0f
            if (b0 & 0x70) or (b1 & 0x80):
                raise OSError("ws-protocol")            # Erweiterungen und maskierte Server-Rahmen gibt es nicht
            ln, off = b1 & 0x7f, 2
            if ln == 126:
                if not self._need(4, deadline):
                    return None
                ln, off = int.from_bytes(self.buf[2:4], "big"), 4
            elif ln == 127:
                if not self._need(10, deadline):
                    return None
                ln, off = int.from_bytes(self.buf[2:10], "big"), 10
            if ln > self.MAX or (op >= 8 and (not fin or ln > 125)):
                raise OSError("ws-protocol")
            if not self._need(off + ln, deadline):
                return None
            payload = bytes(self.buf[off:off + ln])
            del self.buf[:off + ln]
            if op == 9:
                self.sock.sendall(self.frame(10, payload))
                continue
            if op == 10:
                continue
            if op == 8:
                raise EOFError("ws-closed")
            if op in (1, 2):
                if self.first is not None:
                    raise OSError("ws-protocol")
                self.first = op
            elif op == 0:
                if self.first is None:
                    raise OSError("ws-protocol")
            else:
                raise OSError("ws-protocol")
            self.parts += payload
            if len(self.parts) > self.MAX:
                raise OSError("ws-too-big")
            if fin:
                data, kind = bytes(self.parts), self.first
                self.parts, self.first = bytearray(), None
                if kind != 1:
                    raise OSError("ws-not-text")
                return data.decode("utf-8")


class TwitchEvents:
    """Live-Ereignisse des eigenen Kanals über EventSub (WebSocket, Twitch-Anleitung): Follows und Kanalpunkte-Einlösungen. Subs, Geschenk-Abos, Raids und Cheers liest der
    Chat schon vollständiger (EventSub meldet zum Beispiel Resubs ohne Text nicht), darum kommen sie weiter aus dem Chat und werden nicht doppelt gezeigt.

    Ablauf: verbinden (über die Sendewege wie der Chat), auf session_welcome warten, dann in einem eigenen Faden (damit Ping und Keepalive weiterlaufen) die Abonnements
    anlegen, je eines für sich: fehlt ein Recht, fällt nur dieses weg. Benachrichtigungen werden über message_id entdoppelt (Twitch liefert "mindestens einmal"). Bleibt
    die Gegenstelle länger als das 1,5-fache des Keepalive-Werts still, wird neu verbunden; bei session_reconnect geht es zur genannten Adresse, ohne neu zu abonnieren.
    Die Verbindung steht nur, solange die Oberfläche den Chat abfragt, nur für den eigenen Kanal und nur mit den nötigen Rechten. Der Zugangsschlüssel kommt jedes Mal
    frisch aus TwitchLogin (wird erneuert) und steht nie in einem Protokoll."""
    HOST, PORT, PATH = "eventsub.wss.twitch.tv", 443, "/ws?keepalive_timeout_seconds=30"
    SUBS = (("channel.follow", "2", "moderator:read:followers"),
            ("channel.channel_points_custom_reward_redemption.add", "1", "channel:read:redemptions"))
    ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,100}")
    KEEP_MIN = 10.0                       # kleinster erlaubter Keepalive-Wert in Sekunden (Tests setzen ihn kleiner)
    RETRY_FIRST = 5.0                     # Wartezeit vor dem ersten neuen Versuch (danach verdoppelt, höchstens 60 s)
    SUB_DELAYS = (0.0, 5.0, 15.0)         # Abonnieren: sofort, dann was fehlt nach 5 und nach weiteren 15 Sekunden noch einmal

    def __init__(self, store, account, mod, reader, paths=None, host=None, port=None, tls=True, clock=time.monotonic, sleep=time.sleep):
        self.store, self.account, self.mod, self.reader = store, account, mod, reader
        self.paths = paths or getattr(account, "paths", None) or ChatPaths()
        self.host, self.port, self.tls = host or self.HOST, port or self.PORT, tls
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.thread, self.gen, self.sid = None, 0, None
        self.state, self.error, self.types = "aus", "", set()
        self.seen, self.seen_set = collections.deque(maxlen=300), set()

    # ---- Voraussetzungen (ohne Sperre und ohne Anfrage an Twitch: wird von der Oberfläche alle paar Sekunden aufgerufen)
    def granted(self):
        """Die Abonnements, die der Zugang erlaubt, und die Kennung des Kontos (oder ([], ""))."""
        try:
            if not self.account.ready():
                return [], ""
            have = set(self.account.status().get("scopes") or [])
            mine = self.account.login()
            if str((self.store.data or {}).get("channel") or mine).lower() != mine:
                return [], ""                                           # fremder Kanal: die Rechte gelten nur für den eigenen
            return [t for t in self.SUBS if t[2] in have], self.account.user_id()
        except Exception:
            return [], ""

    def status(self):
        subs, uid = self.granted()
        return {"state": self.state if subs and uid else "aus", "types": sorted(self.types), "error": self.error if self.state == "fehler" else ""}

    # ---- Faden
    def ensure(self):
        subs, uid = self.granted()
        if not subs or not uid:
            return
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
            self.gen += 1
            gen = self.gen
            self.state, self.error, self.types, self.sid = "verbinde", "", set(), None
            t = threading.Thread(target=self._run, args=(gen,), daemon=True)
            self.thread = t
        t.start()

    def _alive(self, gen):
        return self.gen == gen and not self.reader._idle() and bool(self.granted()[0])

    def _run(self, gen):
        wait, url = self.RETRY_FIRST, None
        while self._alive(gen):
            began = self.clock()
            try:
                self._session(gen)
            except Exception as e:
                with self.lock:
                    if self.gen == gen:
                        self.state, self.error = "fehler", ("Keine Verbindung zu Twitch" if isinstance(e, (OSError, EOFError)) else "Ereignisse gestört")
            if self.clock() - began > 60:
                wait = self.RETRY_FIRST
            end = self.clock() + wait
            while self.clock() < end and self._alive(gen):
                self.sleep(0.5)
            wait = min(wait * 2, 60.0)
        with self.lock:
            if self.gen == gen:
                self.state, self.types, self.sid = "aus", set(), None

    @classmethod
    def _split_url(cls, url, allow=None):
        """(Host, Pfad samt Abfrage) einer wss-Adresse von Twitch; alles andere (auch fremde Hosts mit Twitch im Namen) ist ein Fehler. Twitch schickt die
        Adresse bei session_reconnect mit und ohne Pfad ("wss://host/ws?x=1" oder "wss://host?x=1")."""
        m = re.fullmatch(r"wss://([A-Za-z0-9.\-]{1,100})(/[A-Za-z0-9_\-./%]{0,200})?(\?[A-Za-z0-9_\-./=&%]{0,300})?", url or "")
        if not m or not (m.group(1) == (allow or cls.HOST) or m.group(1).endswith(".twitch.tv")):
            raise OSError("ws-bad-url")
        path = (m.group(2) or "") + (m.group(3) or "")
        return m.group(1), path if path.startswith("/") else "/" + path

    def _open(self, host, path):
        """Verbindung über die Sendewege, TLS, Aufbau; liefert (WebSocket, Weg)."""
        sock, src = self.paths.connect(host, self.port, timeout=8)
        try:
            if self.tls:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            return WebSocketLite.handshake(sock, host, path, self.clock), src
        except BaseException:
            try:
                sock.close()
            except OSError:
                pass
            raise

    @staticmethod
    def _welcome(payload):
        sess = payload.get("session") or {}
        sid = sess.get("id")
        if not (isinstance(sid, str) and TwitchEvents.ID_RE.fullmatch(sid)):
            raise OSError("ws-bad-session")
        try:
            keep = float(sess.get("keepalive_timeout_seconds") or 30)
        except (TypeError, ValueError):
            keep = 30.0
        return sid, keep

    def _session(self, gen):
        ws, src = self._open(self.host, self.PATH)
        try:
            tok = self.account.token()                                   # einmal vor dem Abonnieren: kein Warten hinter einer Erneuerung, wenn Twitch die 10 Sekunden zählt
            heard, keep, sig = self.clock(), 30.0, None
            while self._alive(gen):
                msg = ws.recv_message(1.0)
                heard = max(heard, ws.last_rx)                           # auch ein Ping der Gegenstelle zeigt, dass sie lebt
                if msg is None:
                    if self.clock() - heard > keep * 1.5:
                        self.paths.fail(src, 120.0)
                        raise OSError("still")
                    if sig is not None and tuple(t[0] for t in self.granted()[0]) != sig:
                        return                                           # Rechte haben sich geändert: neue Sitzung, neu abonnieren
                    continue
                try:
                    d = json.loads(msg)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                meta, payload = d.get("metadata") or {}, d.get("payload") or {}
                typ = meta.get("message_type")
                if typ == "session_welcome":
                    sid, k = self._welcome(payload)
                    keep, heard = min(600.0, max(self.KEEP_MIN, k)), self.clock()
                    sig = tuple(t[0] for t in self.granted()[0])
                    with self.lock:
                        self.sid, self.types = sid, set()
                    threading.Thread(target=self._subscribe, args=(gen, sid, tok, src), daemon=True).start()          # eigener Faden: die Empfangsschleife bleibt frei
                elif typ == "session_reconnect":
                    # Vorgehen laut Twitch: erst die neue Verbindung aufbauen und ihr session_welcome abwarten, dann die alte schließen (die Abonnements ziehen mit um)
                    host, path = self._split_url((payload.get("session") or {}).get("reconnect_url"), self.host)
                    ws2, src2 = self._open(host, path)
                    try:
                        end = self.clock() + 10.0
                        while True:
                            m2 = ws2.recv_message(1.0)
                            if m2 is not None:
                                d2 = json.loads(m2)
                                if isinstance(d2, dict) and (d2.get("metadata") or {}).get("message_type") == "session_welcome":
                                    sid2, k2 = self._welcome(d2.get("payload") or {})
                                    keep = min(600.0, max(self.KEEP_MIN, k2))
                                    break
                            if self.clock() > end:
                                raise OSError("still")
                    except BaseException:
                        ws2.close()
                        raise
                    ws.close()
                    ws, src, heard = ws2, src2, self.clock()
                    with self.lock:
                        self.sid = sid2
                elif typ == "notification":
                    self._notify(meta, payload)
                elif typ == "revocation":
                    sub = (payload.get("subscription") or {}).get("type")
                    with self.lock:
                        self.types.discard(sub)
                        gone = not self.types
                    if gone:
                        return                                           # nichts mehr abonniert: neue Sitzung, neu abonnieren
        finally:
            ws.close()

    def _subscribe(self, gen, sid, tok, src):
        """Abonnements anlegen: beide gleichzeitig, mit kurzer Zeitgrenze und zuerst über den Weg, auf dem die Verbindung steht (Twitch schließt nach 10 s ohne Abonnement);
        was scheitert, wird nach 5 und nach 20 Sekunden noch einmal versucht. Schreibt nur, solange diese Sitzung die aktuelle ist."""
        subs, uid = self.granted()
        todo, last = {t[0]: t for t in subs}, ""
        for delay in self.SUB_DELAYS:
            end = self.clock() + delay
            while self.clock() < end and todo:
                self.sleep(0.2)
                if not self._alive(gen) or self.sid != sid:
                    return
            if not todo or not self._alive(gen) or self.sid != sid:
                break
            results = {}

            def one(typ, ver):
                cond = {"broadcaster_user_id": uid}
                if typ == "channel.follow":
                    cond["moderator_user_id"] = uid
                try:
                    self.mod._call("POST", "eventsub/subscriptions", None,
                                   {"type": typ, "version": ver, "condition": cond, "transport": {"method": "websocket", "session_id": sid}},
                                   timeout=4.0, token=tok, prefer=src, accept=(409,))        # 409: gibt es schon (eine verlorene Antwort wurde wiederholt)
                    results[typ] = ""
                except ValueError as e:
                    results[typ] = str(e) if str(e) == self.mod.ERR_NET else "Twitch hat die Ereignisse nicht angenommen"
            ths = [threading.Thread(target=one, args=(t[0], t[1]), daemon=True) for t in todo.values()]
            for th in ths:
                th.start()
            for th in ths:
                th.join(8.0)
            for typ, err in results.items():
                if err == "":
                    todo.pop(typ, None)
                    with self.lock:
                        if self.sid == sid:
                            self.types.add(typ)
                else:
                    last = err
            with self.lock:
                if self.gen != gen or self.sid != sid:
                    return
                if self.types:
                    self.state, self.error = "ok", ""
                elif not todo or delay == self.SUB_DELAYS[-1]:
                    self.state, self.error = "fehler", last or "Twitch hat die Ereignisse nicht angenommen"
            if not todo:
                break

    # ---- Benachrichtigungen
    def _notify(self, meta, payload):
        mid = meta.get("message_id")
        if isinstance(mid, str) and mid:
            with self.lock:
                if mid in self.seen_set:
                    return                                             # Twitch liefert "mindestens einmal": Wiederholung nach einem Neuaufbau
                if len(self.seen) == self.seen.maxlen:
                    self.seen_set.discard(self.seen[0])
                self.seen.append(mid)
                self.seen_set.add(mid)
        item = self.map_event((payload.get("subscription") or {}).get("type"), payload.get("event"))
        if item:
            self.reader._add(item)

    @staticmethod
    def map_event(typ, ev):
        """Ein Ereignis von Twitch -> Eintrag für den Chat (mit "ev" für die farbige Karte) oder None. Alle Texte werden bereinigt und gekürzt."""
        if not isinstance(ev, dict):
            return None
        clean = lambda v, n: TwitchReader._clean(str(v or ""), n)
        who = clean(ev.get("user_name") or ev.get("user_login"), 40)
        if typ == "channel.follow":
            if not who:
                return None
            return {"type": "notice", "name": "", "color": "", "text": who, "emotes": [], "ev": {"k": "follow", "n": 0}}
        if typ == "channel.channel_points_custom_reward_redemption.add":
            rw = ev.get("reward") if isinstance(ev.get("reward"), dict) else {}
            title = clean(rw.get("title"), 80)
            inp = clean(ev.get("user_input"), 200)
            cost = rw.get("cost")
            n = min(int(cost), 100_000_000) if isinstance(cost, int) and not isinstance(cost, bool) and cost >= 0 else 0
            text = ": ".join(x for x in (who, title) if x) + (" – " + inp if inp else "")
            if not text:
                return None
            return {"type": "notice", "name": "", "color": "", "text": text, "emotes": [], "ev": {"k": "points", "n": n}}
        return None


class TwitchNotifier:
    """Hintergrunddienst: schreibt eine Warnung in den Twitch-Chat, wenn der Akku einer DJI-Kamera die Schwelle erreicht (Auftrag des Nutzers).

    Alle 10 s wird je Kamera geprüft: Warnung, wenn der Akkustand bekannt ist, höchstens die Schwelle beträgt, die Kamera nicht lädt, der Messwert höchstens
    5 Minuten alt ist, für sie noch nicht gewarnt wurde und (bei "Nur während der Sendung") gesendet wird. Danach merkt sich der Speicher die Warnung, auch über einen
    Neustart des Dienstes hinweg. Wieder scharf wird eine Kamera, wenn sie lädt oder ihr Akku mindestens 10 Prozentpunkte über der Schwelle liegt (Akku gewechselt).
    Höchstens eine Nachricht je 3 s insgesamt. Schlägt das Senden fehl, folgen Wiederholungen nach 30, 60 und 120 s (dann weiter alle 120 s), höchstens 5 Versuche je
    Warnung; der letzte Fehler steht im Status. Neue Einstellungen starten die Versuche neu. Im Demo-Modus läuft der Dienst nicht und der Test sendet nichts."""
    POLL_S = 10
    GAP_S = 3
    FRESH_S = 300
    BACKOFF_S = (30, 60, 120, 120)         # Wartezeit nach dem 1., 2., 3. und 4. Fehlversuch; nach dem 5. wird aufgegeben
    TEST_EVERY_S = 10
    TEST_TEXT = "Test: IRL4YOU BOX"

    def __init__(self, store, djisvc, cams, send, chat=None, demo=False, mono=time.monotonic, wall=time.time, sleep=time.sleep):
        self.store, self.djisvc, self.cams, self.send = store, djisvc, cams, send
        self.chat, self.demo = chat or TwitchChat(), demo
        self.mono, self.wall, self.sleep = mono, wall, sleep
        self.lock = threading.Lock()           # Zustand (Versuche, letzte Meldung)
        self.net_lock = threading.Lock()       # immer nur eine Verbindung zu Twitch
        self.tries = {}                        # Schlüssel -> {"n": Versuche, "at": nächster Versuch (monotone Zeit) oder None = aufgegeben}
        self.last = None                       # {"ok", "time", "text"}: Ergebnis des letzten Sendens
        self.last_send = None                  # monotone Zeit der letzten Nachricht
        self.test_at = None
        self.stop_ev = threading.Event()

    # ---- Bausteine
    @staticmethod
    def camera_text(name):
        """Kameraname für die Nachricht: ohne Steuerzeichen und Zeilenumbrüche, höchstens 40 Zeichen, nicht mit / oder . am Anfang (ein Chat-Befehl)."""
        t = " ".join(str(name or "").split())
        t = "".join(ch for ch in t if ch.isprintable()).lstrip("/. ")
        return t[:40].strip()

    @staticmethod
    def render(template, name, percent):
        """{Kamera} und {Prozent} in der Nachricht ersetzen (in einem Durchgang: ein Kameraname wird nicht noch einmal durchsucht)."""
        return re.sub(r"\{(Kamera|Prozent)\}", lambda m: name if m.group(1) == "Kamera" else str(percent), template)

    @staticmethod
    def _percent(e):
        v = e.get("battery") if isinstance(e, dict) else None
        return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100 else None

    def _fresh(self, e):
        age = e.get("battery_age")
        return isinstance(age, (int, float)) and not isinstance(age, bool) and 0 <= age <= self.FRESH_S

    def _live(self):
        """Wird gerade gesendet (eigene Sendekette oder ein belacoder der BELABOX-Oberfläche)?"""
        return bool(self.send._active() or belacoder_running())

    def _camera_name(self, key):
        cam = next((c for c in list(self.cams.cams) if c.get("key") == key), None)
        return self.camera_text(cam.get("name") if cam else "") or key

    def _deliver(self, cfg, text, wait=False):
        """Eine Nachricht senden (nie zwei Verbindungen zugleich). wait: den Abstand zur letzten Nachricht abwarten."""
        with self.net_lock:
            if wait and self.last_send is not None:
                left = self.GAP_S - (self.mono() - self.last_send)
                if left > 0:
                    self.sleep(left)
            ok, msg = self.chat.send(cfg["login"], cfg["token"], cfg["channel"], text)
            self.last_send = self.mono()
        return ok, msg

    # ---- Dienst
    def tick(self):
        """Ein Durchlauf: Kameras wieder scharf stellen, deren Akku gewechselt wurde oder die laden, und fällige Warnungen senden."""
        cfg = self.store.notify_settings()
        if not (cfg["enabled"] and cfg["login"] and cfg["channel"] and cfg["token"]):
            with self.lock:
                self.tries.clear()
            return
        extras = self.djisvc.camera_extras() or {}
        thr = cfg["threshold"]
        for key in self.store.warned_keys():
            e = extras.get(key)
            pct = self._percent(e)
            if e and (e.get("charging") is True or (pct is not None and pct >= thr + 10)):
                self.store.unwarn(key)
        live, due = None, []
        for key in sorted(extras):
            e = extras[key]
            pct = self._percent(e)
            if pct is None or pct > thr or e.get("charging") is True or not self._fresh(e) or self.store.is_warned(key):
                continue
            if cfg["only_live"]:
                live = self._live() if live is None else live
                if not live:
                    continue
            due.append((key, pct))
        keys = {d[0] for d in due}
        with self.lock:
            self.tries = {k: v for k, v in self.tries.items() if k in keys}      # was nicht mehr fällig ist, wird nicht wiederholt
        for key, pct in due:
            now = self.mono()
            t = self.tries.get(key)
            if t is not None and (t["at"] is None or now < t["at"]):
                continue                                                  # wartet auf den nächsten Versuch oder ist aufgegeben
            if self.last_send is not None and now - self.last_send < self.GAP_S:
                break                                                     # höchstens eine Nachricht je 3 s: der Rest folgt beim nächsten Durchlauf
            self._warn(cfg, key, pct)

    def _warn(self, cfg, key, pct):
        text = self.render(cfg["message"], self._camera_name(key), pct)
        ok, msg = self._deliver(cfg, text)
        with self.lock:
            if ok:
                self.tries.pop(key, None)
                self.last = {"ok": True, "time": int(self.wall()), "text": text}
            else:
                n = self.tries.get(key, {}).get("n", 0) + 1
                self.tries[key] = {"n": n, "at": self.mono() + self.BACKOFF_S[n - 1] if n <= len(self.BACKOFF_S) else None}
                self.last = {"ok": False, "time": int(self.wall()), "text": msg}
        if ok:
            self.store.mark_warned(key, self.wall())

    def run(self):
        """Läuft im Hintergrund, solange die Oberfläche läuft."""
        while not self.stop_ev.is_set():
            try:
                self.tick()
            except Exception as e:                  # nie den Dienst beenden; vom Text der Ausnahme nur den Typ ausgeben (er könnte Angaben enthalten)
                print("twitch:", type(e).__name__)
            self.stop_ev.wait(self.POLL_S)

    # ---- für die Oberfläche
    def status(self):
        out = self.store.public()
        with self.lock:
            last = dict(self.last) if self.last else None
            waits = [t["at"] for t in self.tries.values() if t["at"] is not None]
            gave_up = any(t["at"] is None for t in self.tries.values())
        st = {"ok": None, "time": None, "text": "", "retry_at": None, "gave_up": gave_up}
        if last:
            st.update(ok=last["ok"], time=last["time"], text=last["text"])
            if not last["ok"] and waits:
                st["retry_at"] = int(self.wall() + max(0.0, min(waits) - self.mono()))
        out["status"] = st
        return out

    def save(self, req):
        self.store.set(req)
        with self.lock:
            self.tries.clear()                       # neue Angaben: neue Versuche
        return self.status()

    def test(self):
        """Eine Testnachricht mit den gespeicherten Angaben senden: {"ok": bool, "message": Meldung auf Deutsch}."""
        cfg = self.store.notify_settings()
        if not (cfg["channel"] and cfg["login"] and cfg["token"]):
            raise ValueError(TwitchStore.ERR_MISSING)
        with self.lock:
            now = self.mono()
            if self.test_at is not None and now - self.test_at < self.TEST_EVERY_S:
                raise PermissionError("Bitte kurz warten")
            self.test_at = now
        ok, msg = (True, "Demo: gesendet.") if self.demo else self._deliver(cfg, self.TEST_TEXT, wait=True)
        with self.lock:
            self.last = {"ok": ok, "time": int(self.wall()), "text": self.TEST_TEXT if ok else msg}
        return {"ok": ok, "message": msg}


class Vault:
    """Einstellungen mit einem Passwort verschlüsseln und prüfen (Issue #20). Nur die Standardbibliothek:
      * Schlüssel aus dem Passwort: PBKDF2-HMAC-SHA256, 600000 Runden, 16 Byte Salz, 64 Byte Ergebnis (32 für AES, 32 für die Prüfsumme).
      * Verschlüsselung: AES-256 im Zählerbetrieb (CTR), Zähler = 12 Byte Nonce + 4 Byte Zähler ab 0. CTR braucht nur die Richtung "Verschlüsseln";
        diese steht hier selbst (tabellenbasiert) und ist in tools/test_settings.py gegen FIPS 197 und NIST SP 800-38A geprüft.
      * Echtheit: HMAC-SHA256 über Kennung, Rundenzahl, Salz, Nonce und Daten (erst verschlüsseln, dann prüfen). Ein falsches Passwort oder eine
        veränderte Datei fällt dadurch sicher auf, bevor etwas entschlüsselt wird."""
    ITERATIONS = 600000
    MIN_ITER, MAX_ITER = 100000, 2000000          # was eine Datei verlangen darf (sonst ließe sich die Box mit einer Datei ausbremsen)
    TAG = b"IRL4YOU-BOX-SETTINGS-1|"
    MIN_PASSWORD, MAX_PASSWORD = 8, 128
    _TABLES = None

    @classmethod
    def _tables(cls):
        """S-Box und die vier Rundentabellen von AES (aus der Definition berechnet, nicht abgetippt)."""
        if cls._TABLES:
            return cls._TABLES
        sbox = [0] * 256
        rot = lambda v, n: ((v << n) | (v >> (8 - n))) & 0xFF
        p = q = 1
        while True:
            p = (p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)) & 0xFF          # p mal 3 im Körper GF(2^8)
            q ^= (q << 1) & 0xFF
            q ^= (q << 2) & 0xFF
            q ^= (q << 4) & 0xFF
            if q & 0x80:
                q ^= 0x09                                                          # q durch 3 teilen
            sbox[p] = (q ^ rot(q, 1) ^ rot(q, 2) ^ rot(q, 3) ^ rot(q, 4) ^ 0x63) & 0xFF
            if p == 1:
                break
        sbox[0] = 0x63
        t0 = []
        for a in range(256):
            s = sbox[a]
            s2 = ((s << 1) ^ (0x1B if s & 0x80 else 0)) & 0xFF
            t0.append((s2 << 24) | (s << 16) | (s << 8) | (s2 ^ s))
        ror = lambda w, n: ((w >> n) | (w << (32 - n))) & 0xFFFFFFFF
        cls._TABLES = (sbox, t0, [ror(w, 8) for w in t0], [ror(w, 16) for w in t0], [ror(w, 24) for w in t0])
        return cls._TABLES

    @classmethod
    def _round_keys(cls, key):
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("AES-256 braucht einen Schlüssel von 32 Byte")
        sbox = cls._tables()[0]
        sub = lambda w: (sbox[w >> 24] << 24) | (sbox[(w >> 16) & 255] << 16) | (sbox[(w >> 8) & 255] << 8) | sbox[w & 255]
        w = [int.from_bytes(key[i:i + 4], "big") for i in range(0, 32, 4)]
        rcon = 1
        for i in range(8, 60):
            t = w[i - 1]
            if i % 8 == 0:
                t = sub(((t << 8) | (t >> 24)) & 0xFFFFFFFF) ^ (rcon << 24)
                rcon = ((rcon << 1) ^ (0x11B if rcon & 0x80 else 0)) & 0xFF
            elif i % 8 == 4:
                t = sub(t)
            w.append(w[i - 8] ^ t)
        return w

    @classmethod
    def aes256_block(cls, rk, block):
        """Einen Block (16 Byte) mit AES-256 verschlüsseln. rk: Rundenschlüssel aus _round_keys."""
        sbox, t0, t1, t2, t3 = cls._tables()
        s0, s1, s2, s3 = (int.from_bytes(block[i:i + 4], "big") ^ rk[i // 4] for i in (0, 4, 8, 12))
        for r in range(1, 14):
            k = 4 * r
            s0, s1, s2, s3 = (t0[s0 >> 24] ^ t1[(s1 >> 16) & 255] ^ t2[(s2 >> 8) & 255] ^ t3[s3 & 255] ^ rk[k],
                              t0[s1 >> 24] ^ t1[(s2 >> 16) & 255] ^ t2[(s3 >> 8) & 255] ^ t3[s0 & 255] ^ rk[k + 1],
                              t0[s2 >> 24] ^ t1[(s3 >> 16) & 255] ^ t2[(s0 >> 8) & 255] ^ t3[s1 & 255] ^ rk[k + 2],
                              t0[s3 >> 24] ^ t1[(s0 >> 16) & 255] ^ t2[(s1 >> 8) & 255] ^ t3[s2 & 255] ^ rk[k + 3])
        k = 56
        out = ((sbox[s0 >> 24] << 24) | (sbox[(s1 >> 16) & 255] << 16) | (sbox[(s2 >> 8) & 255] << 8) | sbox[s3 & 255]) ^ rk[k], \
              ((sbox[s1 >> 24] << 24) | (sbox[(s2 >> 16) & 255] << 16) | (sbox[(s3 >> 8) & 255] << 8) | sbox[s0 & 255]) ^ rk[k + 1], \
              ((sbox[s2 >> 24] << 24) | (sbox[(s3 >> 16) & 255] << 16) | (sbox[(s0 >> 8) & 255] << 8) | sbox[s1 & 255]) ^ rk[k + 2], \
              ((sbox[s3 >> 24] << 24) | (sbox[(s0 >> 16) & 255] << 16) | (sbox[(s1 >> 8) & 255] << 8) | sbox[s2 & 255]) ^ rk[k + 3]
        return b"".join(v.to_bytes(4, "big") for v in out)

    @classmethod
    def ctr(cls, key, counter, data):
        """Zählerbetrieb: data mit dem Schlüsselstrom ab dem 128-Bit-Zählerwert counter (Zahl) verknüpfen. Ver- und Entschlüsseln sind dasselbe."""
        rk = cls._round_keys(key)
        out = bytearray()
        for i in range(0, len(data), 16):
            ks = cls.aes256_block(rk, ((counter + i // 16) & ((1 << 128) - 1)).to_bytes(16, "big"))
            chunk = data[i:i + 16]
            n = len(chunk)
            out += (int.from_bytes(chunk, "big") ^ int.from_bytes(ks[:n], "big")).to_bytes(n, "big")
        return bytes(out)

    @classmethod
    def _keys(cls, password, salt, iterations):
        k = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, 64)
        return k[:32], k[32:]

    @classmethod
    def _mac(cls, mac_key, iterations, salt, nonce, data):
        return hmac.new(mac_key, cls.TAG + iterations.to_bytes(8, "big") + salt + nonce + data, hashlib.sha256).digest()

    @classmethod
    def check_password(cls, password):
        if not isinstance(password, str) or not cls.MIN_PASSWORD <= len(password) <= cls.MAX_PASSWORD:
            raise ValueError("Passwort: %d bis %d Zeichen" % (cls.MIN_PASSWORD, cls.MAX_PASSWORD))

    @classmethod
    def seal(cls, plain, password, header, iterations=None):
        """plain (Bytes) verschlüsseln. header: Angaben, die unverschlüsselt in der Datei stehen (Kennung, Version, Zeit). Gibt die Datei als dict zurück."""
        cls.check_password(password)
        iterations = iterations or cls.ITERATIONS
        salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
        enc_key, mac_key = cls._keys(password, salt, iterations)
        data = cls.ctr(enc_key, int.from_bytes(nonce + b"\0\0\0\0", "big"), plain)
        b64 = lambda b: base64.b64encode(b).decode("ascii")
        return dict(header, encrypted=True, cipher="AES-256-CTR", kdf="PBKDF2-HMAC-SHA256", iterations=iterations,
                    salt=b64(salt), nonce=b64(nonce), data=b64(data), mac=b64(cls._mac(mac_key, iterations, salt, nonce, data)))

    @classmethod
    def open(cls, doc, password):
        """Verschlüsselte Datei (dict) öffnen: Bytes oder ValueError ("Passwort falsch oder Datei verändert")."""
        cls.check_password(password)
        try:
            if doc.get("cipher") != "AES-256-CTR" or doc.get("kdf") != "PBKDF2-HMAC-SHA256":
                raise ValueError
            iterations = doc.get("iterations")
            if isinstance(iterations, bool) or not isinstance(iterations, int) or not cls.MIN_ITER <= iterations <= cls.MAX_ITER:
                raise ValueError
            salt, nonce, data, mac = (base64.b64decode(str(doc.get(k, "")), validate=True) for k in ("salt", "nonce", "data", "mac"))
            if len(salt) != 16 or len(nonce) != 12 or len(mac) != 32:
                raise ValueError
        except (ValueError, TypeError, binascii.Error):
            raise ValueError("Die Datei ist keine gültige verschlüsselte Sicherung")
        enc_key, mac_key = cls._keys(password, salt, iterations)
        if not hmac.compare_digest(mac, cls._mac(mac_key, iterations, salt, nonce, data)):
            raise ValueError("Das Passwort ist falsch oder die Datei wurde verändert")
        return cls.ctr(enc_key, int.from_bytes(nonce + b"\0\0\0\0", "big"), data)


SETTINGS_FORMAT = "irl4you-box-einstellungen"
SETTINGS_VERSION = 1
SETTINGS_SECTIONS = (("cameras", "Kameras"), ("pipeline", "Bildaufbau"), ("srtla", "SRTLA-Server und Sendeeinstellungen"),
                     ("autostart", "Automatischer Start"), ("names", "Namen (Verbindungen, WLAN- und Bluetooth-Sticks)"),
                     ("dji", "DJI-Kameras (Einstellungen)"), ("hdmi", "HDMI-Eingang (Einstellungen)"), ("twitch", "Akku-Warnung im Twitch-Chat (Einstellungen)"),
                     ("camnet", "Netzwerk für Kameras (Standard)"),
                     ("hotspots", "Hotspots"), ("wifi", "Gespeicherte WLAN-Netze"))
IFACE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")


class SettingsTransfer:
    """Einstellungen in eine Datei sichern und wieder einspielen (Issue #20), zum Beispiel nach dem Neu-Aufspielen der SD-Karte.

    Gesichert werden Kameras, Bildaufbau, SRTLA-Server und Sendeeinstellungen, automatischer Start, Namen der Sticks, DJI-Kameras (ihre Einstellungen, nicht die
    Bluetooth-Kopplung), der HDMI-Eingang, die Akku-Warnung im Twitch-Chat (ohne Token und Anmeldung), das Netzwerk der Kameras, die Optionen der Oberfläche (Reihenfolge
    und Ausblenden der Menüs), Hotspots und die gespeicherten WLAN-Netze. Nie dabei: das Passwort dieser Oberfläche, SSH, Schlüssel, die Anmeldung der Fernfreigabe,
    Token und Anmeldung bei Twitch.
    Eine Datei mit Passwörtern und Zugangsdaten wird immer mit einem Passwort verschlüsselt (Vault). Beim Einspielen wird jeder Teil streng geprüft; die
    Prüfung der einzelnen Speicher (Kameras, Bildaufbau, SRTLA, Hotspots, WLAN-Helfer) gilt dabei wie bei der Eingabe von Hand. Eingespielt wird nur, wenn
    nicht gesendet wird, und der Stand davor wird gesichert (Rückgängig)."""
    MAX_BODY = 262144

    def __init__(self, state_dir, cams, pipeline, srtla, autostart, names, djisvc, wifi, send, demo=False):
        self.cams, self.pipeline, self.srtla, self.autostart = cams, pipeline, srtla, autostart
        self.names, self.djisvc, self.wifi, self.send, self.demo = names, djisvc, wifi, send, demo
        self.hdmi = self.twitch = self.netchoice = None          # werden nach dem Start gesetzt (entstehen später oder fehlen in Tests)
        self.backup_dir = os.path.join(state_dir, "backup")
        self.backup_path = os.path.join(self.backup_dir, "vor-einspielen.json")
        self.version = (read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION"), "") or "").strip()

    # ------------------------------------------------------------------ Sichern
    def make_document(self, secrets_on, wifi_data=None):
        """Die Einstellungen dieser Box als dict. secrets_on: Passwörter und Zugangsdaten mitnehmen. wifi_data: Ergebnis des WLAN-Helfers oder None."""
        doc = {"format": SETTINGS_FORMAT, "version": SETTINGS_VERSION, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "box_version": self.version, "secrets": bool(secrets_on)}
        with self.cams.lock:
            doc["cameras"] = [dict({"name": c["name"], "key": c["key"], "role": c["role"]}, **({"iface": c["iface"]} if c.get("iface") else {}))
                              for c in self.cams.cams]
        doc["pipeline"] = {k: v for k, v in self.pipeline.cfg.items()}
        with self.srtla.lock:
            data = self.srtla.data
            servers = [dict({"name": s["name"], "host": s["host"], "port": s["port"]}, **({"streamid": s.get("streamid", "")} if secrets_on else {}))
                       for s in data["servers"]]
            sel = next((i for i, s in enumerate(data["servers"]) if s["id"] == data.get("selected")), None)
            doc["srtla"] = {"servers": servers, "selected": sel, "settings": dict(data["settings"])}
        doc["autostart"] = {"enabled": bool(self.autostart.enabled())}
        doc["names"] = self.names._all()
        dj = []
        for addr, c in sorted(self.djisvc._config().items()):
            if isinstance(c, dict):
                e = {k: c.get(k) for k in ("name", "model", "kind", "wifi_ifname", "ip", "resolution", "fps", "bitrate", "stabilization", "autoconnect", "ssid", "status_only")
                     if c.get(k) is not None}
                e["addr"] = str(addr).upper()
                if secrets_on and c.get("password"):
                    e["password"] = c["password"]
                dj.append(e)
        doc["dji"] = dj
        hs = {}
        for iface, h in sorted(self.wifi.hotspots().items()):
            e = {k: h.get(k) for k in ("ssid", "band", "channel") if h.get(k) is not None}
            if secrets_on and h.get("password"):
                e["password"] = h["password"]
            hs[iface] = e
        doc["hotspots"] = hs
        if self.hdmi is not None:
            try:
                st = self.hdmi.status()
            except Exception:                                                # der HDMI-Dienst fehlt: der Teil bleibt weg
                st = {}
            if st.get("service"):
                doc["hdmi"] = {k: st["settings"][k] for k in HdmiService.ALLOWED}
        if self.twitch is not None:
            with self.twitch.lock:                                           # ohne Token und Anmeldung: beides bleibt auf der Box
                t = self.twitch.data
                doc["twitch"] = {"enabled": bool(t["enabled"]), "channel": t["channel"], "login": t["login"], "threshold": t["threshold"],
                                 "message": t["message"], "only_live": bool(t["only_live"])}
        if self.netchoice is not None and self.netchoice.iface:
            doc["camnet"] = {"iface": self.netchoice.iface}
        if wifi_data is not None:
            doc["wifi"] = {"networks": [{k: v for k, v in n.items() if secrets_on or k != "password"} for n in wifi_data.get("networks", [])],
                           "skipped": list(wifi_data.get("skipped", []))}
        return doc

    def export(self, secrets_on, password=None, with_wifi=True):
        """Datei zum Herunterladen: {"document": dict, "encrypted": bool, "notes": [..]}. Mit Passwörtern nur verschlüsselt."""
        if secrets_on and not password:
            raise ValueError("Eine Datei mit Passwörtern wird immer verschlüsselt: bitte ein Passwort zum Verschlüsseln eingeben")
        if password:
            Vault.check_password(password)
        notes, wifi_data = [], None
        if with_wifi:
            try:
                wifi_data = self.wifi.export_saved(secrets_on)
            except (ValueError, RuntimeError) as e:
                notes.append("Die gespeicherten WLAN-Netze sind nicht dabei: " + str(e))
        doc = self.make_document(secrets_on, wifi_data)
        if wifi_data and wifi_data.get("skipped"):
            notes.append("Nicht übertragbar: " + ", ".join("„%s“ (%s)" % (s.get("ssid", "?"), s.get("why", "?")) for s in wifi_data["skipped"][:10]))
        header = {k: doc[k] for k in ("format", "version", "created", "box_version", "secrets")}
        if password:
            out = Vault.seal(json.dumps(doc, ensure_ascii=False).encode("utf-8"), password, header)
        else:
            out = doc
        return {"document": out, "encrypted": bool(password), "notes": notes}

    # ------------------------------------------------------------------ Lesen und Prüfen
    def read_document(self, raw, password=None):
        """Die Datei (geparstes JSON) lesen, bei Bedarf entschlüsseln. Gibt (Einstellungen, verschlüsselt?) zurück."""
        if not isinstance(raw, dict) or raw.get("format") != SETTINGS_FORMAT:
            raise ValueError("Das ist keine Sicherung der IRL4YOU BOX")
        ver = raw.get("version")
        if isinstance(ver, bool) or not isinstance(ver, int) or ver < 1:
            raise ValueError("Die Version der Sicherung ist ungültig")
        if ver > SETTINGS_VERSION:
            raise ValueError("Die Sicherung stammt von einer neueren Version (%s). Bitte zuerst die Box aktualisieren." % ver)
        if len(json.dumps(raw)) > self.MAX_BODY:
            raise ValueError("Die Datei ist zu groß")
        if raw.get("encrypted") is True:
            if not password:
                raise ValueError("Die Datei ist verschlüsselt: bitte das Passwort eingeben")
            try:
                doc = json.loads(Vault.open(raw, password).decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                if isinstance(e, ValueError) and ("Passwort" in str(e) or "Datei" in str(e)):
                    raise
                raise ValueError("Der Inhalt der Sicherung ist beschädigt")
            if not isinstance(doc, dict) or doc.get("format") != SETTINGS_FORMAT or doc.get("version") != ver:
                raise ValueError("Der Inhalt der Sicherung passt nicht zur Kennung")
            return doc, True
        if raw.get("secrets") is True:
            raise ValueError("Eine Sicherung mit Passwörtern muss verschlüsselt sein")
        return raw, False

    @staticmethod
    def _text(v, lo, hi, what):
        if not isinstance(v, str) or not lo <= len(v.strip()) <= hi or any(not ch.isprintable() for ch in v):
            raise ValueError("%s ungültig" % what)
        return v.strip()

    def _clean_cameras(self, raw):
        if not isinstance(raw, list) or len(raw) > 40:
            raise ValueError("Die Kameraliste ist ungültig (höchstens 40 Kameras)")
        out, keys, roles, names, notes = [], set(), set(), set(), []
        for c in raw:
            if not isinstance(c, dict):
                raise ValueError("Die Kameraliste ist ungültig")
            name = self._text(c.get("name"), 1, 40, "Kameraname")
            key = c.get("key")
            if not isinstance(key, str) or not KEY_RE.match(key):
                raise ValueError("Ein Kamera-Schlüssel ist ungültig")
            if key in keys:
                raise ValueError("Der Kamera-Schlüssel „%s“ kommt doppelt vor" % key)
            role = c.get("role") if c.get("role") in ROLES else "extra"
            if role in ("main", "pip") and role in roles:
                role = "extra"
                notes.append("Rolle von „%s“ auf „extra“ gestellt (die Rolle war doppelt)" % name)
            if name.casefold() in names:
                name = (name[:34] + " " + key[-5:]).strip()
            iface = c.get("iface") if isinstance(c.get("iface"), str) and IFACE_NAME_RE.match(c.get("iface", "")) else ""
            keys.add(key)
            roles.add(role)
            names.add(name.casefold())
            out.append({"name": name, "key": key, "role": role, "iface": iface})
        return out, notes

    def _clean_pipeline(self, raw, keys):
        if not isinstance(raw, dict):
            raise ValueError("Der Bildaufbau ist ungültig")
        tmp = PipelineStore(os.devnull)                     # prüft wie das Formular, schreibt nichts
        tmp.save = lambda: None
        tmp.set(dict(raw), list(keys))
        return dict(raw), []

    def _clean_srtla(self, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("servers"), list) or len(raw["servers"]) > 30:
            raise ValueError("Die SRTLA-Server sind ungültig (höchstens 30)")
        servers, notes = [], []
        for s in raw["servers"]:
            if not isinstance(s, dict):
                raise ValueError("Die SRTLA-Server sind ungültig")
            servers.append(SrtlaStore.check({"name": s.get("name"), "host": s.get("host"), "port": s.get("port"), "streamid": s.get("streamid") or ""}))
        sel = raw.get("selected")
        if isinstance(sel, bool) or not (sel is None or isinstance(sel, int)) or (isinstance(sel, int) and not 0 <= sel < len(servers)):
            sel = 0 if servers else None
        st = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
        cur = SrtlaStore.DEFAULT_SETTINGS
        try:
            mn, mx, lat = int(st.get("min_kbps", cur["min_kbps"])), int(st.get("max_kbps", cur["max_kbps"])), int(st.get("latency_ms", cur["latency_ms"]))
        except (TypeError, ValueError):
            raise ValueError("Bitrate und Latenz sind ungültig")
        if not 100 <= mn < mx <= 20000 or not 100 <= lat <= 10000:
            raise ValueError("Bitrate oder Latenz außerhalb des erlaubten Bereichs")
        spread = st.get("spread", "best")
        if spread not in ("best", "all"):
            raise ValueError("Verteilung ungültig")
        ups = st.get("uplinks", [])
        if not isinstance(ups, list) or len(ups) > 20 or any(not isinstance(u, str) or not IFACE_NAME_RE.match(u) for u in ups):
            raise ValueError("Die Netze zum Senden sind ungültig")
        return {"servers": servers, "selected": sel, "settings": {"min_kbps": mn, "max_kbps": mx, "latency_ms": lat, "spread": spread,
                                                                  "uplinks": sorted(set(ups))}}, notes

    def _clean_autostart(self, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("enabled"), bool):
            raise ValueError("Der automatische Start ist ungültig")
        return {"enabled": raw["enabled"]}, []

    def _clean_names(self, raw):
        if not isinstance(raw, dict) or len(raw) > 60:
            raise ValueError("Die Namen der Sticks sind ungültig")
        out = {}
        for k, v in raw.items():
            if not isinstance(k, str) or not DeviceNames.KEY_RE.match(k) or not isinstance(v, str):
                raise ValueError("Die Namen der Sticks sind ungültig")
            v = " ".join(v.split())
            if len(v) > 40 or any(not ch.isprintable() for ch in v):
                raise ValueError("Ein Name eines Sticks ist ungültig")
            if v:
                out[k] = v
        return out, []

    def _clean_dji(self, raw):
        if not isinstance(raw, list) or len(raw) > 20:
            raise ValueError("Die DJI-Kameras sind ungültig (höchstens 20)")
        out, seen = [], set()
        for c in raw:
            if not isinstance(c, dict) or not isinstance(c.get("addr"), str) or not DjiService.MAC_RE.match(c["addr"]):
                raise ValueError("Eine DJI-Kamera hat keine gültige Geräteadresse")
            addr = c["addr"].upper()
            if addr in seen:
                raise ValueError("Eine DJI-Kamera kommt doppelt vor")
            seen.add(addr)
            e = {"addr": addr, "name": self._text(c.get("name") or c.get("model") or addr, 1, 40, "Kameraname"),
                 "model": self._text(c.get("model") or "", 0, 60, "Modell"), "kind": c.get("kind") if isinstance(c.get("kind"), str) and re.fullmatch(r"[a-z0-9_]{1,20}", c["kind"]) else "unknown"}
            wi = c.get("wifi_ifname", "")
            if wi not in ("", "manual") and not (isinstance(wi, str) and IFACE_NAME_RE.match(wi)):
                raise ValueError("Die Verbindung einer DJI-Kamera ist ungültig")
            e["wifi_ifname"] = wi
            ip = c.get("ip", "")
            if ip:
                try:
                    ipaddress.IPv4Address(ip)
                except (ValueError, TypeError):
                    raise ValueError("Die Adresse einer DJI-Kamera ist ungültig")
            e["ip"] = ip or ""
            ssid = c.get("ssid", "")
            if not isinstance(ssid, str) or len(ssid.encode("utf-8")) > 32 or any(ord(ch) < 32 or ord(ch) == 127 for ch in ssid):
                raise ValueError("Das WLAN einer DJI-Kamera ist ungültig")
            e["ssid"] = ssid
            pw = c.get("password", "")
            if not isinstance(pw, str) or len(pw) > 64 or any(ord(ch) < 32 or ord(ch) == 127 for ch in pw):
                raise ValueError("Das WLAN-Passwort einer DJI-Kamera ist ungültig")
            if pw:
                e["password"] = pw
            for k, lo, hi in (("fps", 1, 120), ("bitrate", 100, 40000)):
                v = c.get(k)
                if v is not None:
                    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                        raise ValueError("Ein Wert einer DJI-Kamera ist ungültig (%s)" % k)
                    e[k] = v
            for k in ("resolution", "stabilization"):
                v = c.get(k)
                if v is not None:
                    if not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_+.-]{1,20}", v):
                        raise ValueError("Ein Wert einer DJI-Kamera ist ungültig (%s)" % k)
                    e[k] = v
            if c.get("autoconnect") is not None:
                if not isinstance(c["autoconnect"], bool):
                    raise ValueError("Ein Wert einer DJI-Kamera ist ungültig (autoconnect)")
                e["autoconnect"] = c["autoconnect"]
            if c.get("status_only") is not None:
                if not isinstance(c["status_only"], bool):
                    raise ValueError("Ein Wert einer DJI-Kamera ist ungültig (status_only)")
                e["status_only"] = c["status_only"]
            out.append(e)
        return out, ["Die Bluetooth-Kopplung lässt sich nicht mitnehmen: Die Kameras müssen nach dem Einspielen einmal neu verbunden werden"] if out else []

    def _clean_hdmi(self, raw):
        if not isinstance(raw, dict):
            raise ValueError("Die HDMI-Einstellungen sind ungültig")
        want = {k: raw[k] for k in HdmiService.ALLOWED if k in raw}
        out = hdmi_daemon.clean_settings(want, None)                         # dieselbe Prüfung wie im Dienst (wirft ValueError mit kurzem Text)
        return {k: out[k] for k in HdmiService.ALLOWED}, []

    def _clean_twitch(self, raw):
        if not isinstance(raw, dict):
            raise ValueError("Die Einstellungen der Akku-Warnung sind ungültig")
        for k in ("enabled", "only_live"):
            if not isinstance(raw.get(k), bool):
                raise ValueError("Die Einstellungen der Akku-Warnung sind ungültig")
        out = {"enabled": raw["enabled"], "only_live": raw["only_live"],
               "channel": TwitchStore._name(raw.get("channel", ""), TwitchStore.ERR_CHANNEL), "login": TwitchStore._name(raw.get("login", ""), TwitchStore.ERR_LOGIN),
               "threshold": TwitchStore._threshold(raw.get("threshold")), "message": TwitchStore._message(raw.get("message"))}
        return out, ["Token und Twitch-Anmeldung werden nie mitgenommen: bitte hier neu anmelden"]

    def _clean_camnet(self, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("iface"), str) or not IFACE_NAME_RE.match(raw["iface"]):
            raise ValueError("Das Netzwerk für Kameras ist ungültig")
        return {"iface": raw["iface"]}, []

    def _clean_hotspots(self, raw):
        if not isinstance(raw, dict) or len(raw) > 8:
            raise ValueError("Die Hotspots sind ungültig")
        out, notes = {}, []
        for iface, h in raw.items():
            if not isinstance(iface, str) or not re.fullmatch(r"[a-z][a-z0-9]{1,14}", iface) or not isinstance(h, dict):
                raise ValueError("Die Hotspots sind ungültig")
            ssid, pw, band, ch = h.get("ssid"), h.get("password", ""), h.get("band", "bg"), h.get("channel", 0)
            if (not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or ssid != ssid.strip() or any(ord(c) < 32 or ord(c) == 127 for c in ssid)
                    or ssid.startswith(Wifi.HS_PREFIX)):
                raise ValueError("Der Name eines Hotspots ist ungültig")
            if not isinstance(pw, str) or (pw and (not 8 <= len(pw) <= 63 or any(not 32 <= ord(c) < 127 for c in pw))):
                raise ValueError("Das Passwort eines Hotspots ist ungültig")
            if band not in Wifi.HS_BANDS or isinstance(ch, bool) or not isinstance(ch, int) or (ch != 0 and ch not in Wifi.HS_BANDS[band]):
                raise ValueError("Band oder Kanal eines Hotspots ist ungültig")
            out[iface] = {"ssid": ssid, "password": pw, "band": band, "channel": ch}
        return out, notes

    def _clean_wifi(self, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("networks"), list) or len(raw["networks"]) > 50:
            raise ValueError("Die gespeicherten WLAN-Netze sind ungültig (höchstens 50)")
        out, notes, seen = [], [], set()
        for n in raw["networks"]:
            if not isinstance(n, dict):
                raise ValueError("Die gespeicherten WLAN-Netze sind ungültig")
            ssid, pw = n.get("ssid"), n.get("password", "")
            if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or any(ord(c) < 32 or ord(c) == 127 for c in ssid):
                raise ValueError("Der Name eines WLAN-Netzes ist ungültig")
            if ssid.startswith(Wifi.HS_PREFIX) or ssid in seen:
                continue
            seen.add(ssid)
            if not isinstance(pw, str) or (pw and not (8 <= len(pw) <= 63 and all(32 <= ord(c) < 127 for c in pw) or re.fullmatch(r"[0-9a-fA-F]{64}", pw))):
                raise ValueError("Ein WLAN-Passwort ist ungültig")
            sec = n.get("security", "none" if n.get("open") is True else "wpa-psk")
            if sec not in ("wpa-psk", "sae", "none"):
                raise ValueError("Die Sicherheitsart eines WLAN-Netzes ist ungültig")
            open_net = n.get("open") is True or sec == "none"
            if open_net and pw:
                raise ValueError("Ein offenes WLAN-Netz hat kein Passwort")
            if not pw and not open_net:
                notes.append("„%s“ ohne Passwort gesichert: bitte neu verbinden" % ssid)
                continue
            out.append({"ssid": ssid, "password": pw, "hidden": n.get("hidden") is True, "open": open_net, "security": "none" if open_net else sec})
        return {"networks": out}, notes

    def clean(self, doc):
        """Alle Teile der Einstellungen prüfen. Gibt (geprüfte Teile, Bericht) zurück; ein ungültiger Teil fällt mit Begründung heraus."""
        clean, report, labels = {}, [], dict(SETTINGS_SECTIONS)
        for sid, label in SETTINGS_SECTIONS:
            if sid not in doc:
                continue
            raw = doc[sid]
            try:
                if sid == "cameras":
                    data, notes = self._clean_cameras(raw)
                    count = len(data)
                elif sid == "pipeline":
                    keys = [c["key"] for c in clean["cameras"]] if "cameras" in clean else [c["key"] for c in self.cams.cams]
                    data, notes = self._clean_pipeline(raw, keys)
                    count = 1
                elif sid == "srtla":
                    data, notes = self._clean_srtla(raw)
                    count = len(data["servers"])
                elif sid == "autostart":
                    data, notes = self._clean_autostart(raw)
                    count = 1
                elif sid == "names":
                    data, notes = self._clean_names(raw)
                    count = len(data)
                elif sid == "dji":
                    data, notes = self._clean_dji(raw)
                    count = len(data)
                elif sid == "hdmi":
                    data, notes = self._clean_hdmi(raw)
                    count = 1
                elif sid == "twitch":
                    data, notes = self._clean_twitch(raw)
                    count = 1
                elif sid == "camnet":
                    data, notes = self._clean_camnet(raw)
                    count = 1
                elif sid == "hotspots":
                    data, notes = self._clean_hotspots(raw)
                    count = len(data)
                else:
                    data, notes = self._clean_wifi(raw)
                    count = len(data["networks"])
                clean[sid] = data
                report.append({"id": sid, "label": label, "count": count, "ok": True, "note": "; ".join(notes)})
            except ValueError as e:
                report.append({"id": sid, "label": label, "count": 0, "ok": False, "note": str(e)})
        return clean, report

    def preview(self, raw, password=None):
        doc, encrypted = self.read_document(raw, password)
        _, report = self.clean(doc)
        if not report:
            raise ValueError("In der Datei steht nichts, was sich einspielen lässt")
        return {"ok": True, "encrypted": encrypted, "created": str(doc.get("created", ""))[:32], "box_version": str(doc.get("box_version", ""))[:16],
                "secrets": doc.get("secrets") is True, "sections": report, "has_backup": os.path.isfile(self.backup_path)}

    # ------------------------------------------------------------------ Einspielen
    def _guard(self):
        if self.send._active():
            raise ValueError("Es wird gerade gesendet. Bitte zuerst die Sendung beenden, dann einspielen.")

    def _write_backup(self):
        """Der Stand vor dem Einspielen (ohne die WLAN-Netze, die der Helfer führt), nur für den Benutzer pipbox lesbar."""
        doc = self.make_document(True)
        os.makedirs(self.backup_dir, mode=0o700, exist_ok=True)
        tmp = self.backup_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f)
        os.replace(tmp, self.backup_path)

    def apply(self, raw, password=None, sections=None):
        """Die gewählten Teile einspielen. Gibt {"ok": True, "results": [{id, label, ok, message}], "backup": bool} zurück."""
        self._guard()
        doc, _ = self.read_document(raw, password)
        return self._apply_doc(doc, sections)

    def _apply_doc(self, doc, sections):
        clean, report = self.clean(doc)
        wanted = [s for s, _ in SETTINGS_SECTIONS if s in clean and (sections is None or s in sections)]
        if not wanted:
            raise ValueError("Nichts zum Einspielen gewählt")
        self._write_backup()
        results, labels = [], dict(SETTINGS_SECTIONS)
        for sid in wanted:
            try:
                msg = getattr(self, "_apply_" + sid)(clean[sid])
                results.append({"id": sid, "label": labels[sid], "ok": True, "message": msg})
            except Exception as e:                                  # ein Teil darf nie die anderen oder die Oberfläche mitnehmen
                results.append({"id": sid, "label": labels[sid], "ok": False, "message": str(e) or type(e).__name__})
        if "cameras" in wanted and "dji" in wanted:
            self._restore_camera_names(clean["cameras"])
        for r in report:
            if not r["ok"] and (sections is None or r["id"] in sections):
                results.append({"id": r["id"], "label": r["label"], "ok": False, "message": "Nicht eingespielt: " + r["note"]})
        return {"ok": True, "results": results, "backup": True}

    def _restore_camera_names(self, cams):
        """Die Namen der Kameraliste aus der Sicherung gelten (auch für DJI-Kameras): Beim Einspielen der DJI-Kameras gibt der DJI-Dienst der Kamera in der Liste
        sonst seinen eigenen Namen zurück, und ein in der Liste vergebener Name ginge verloren."""
        want = {c["key"]: c["name"] for c in cams}
        with self.cams.lock:
            changed = False
            for c in self.cams.cams:
                n = want.get(c["key"])
                if n and c["name"] != n:
                    c["name"] = n
                    changed = True
            if changed:
                self.cams.save()

    def restore(self):
        """Den Stand vor dem letzten Einspielen wiederherstellen."""
        self._guard()
        try:
            with open(self.backup_path) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            raise ValueError("Es gibt keinen gesicherten Stand")
        if not isinstance(doc, dict) or doc.get("format") != SETTINGS_FORMAT:
            raise ValueError("Der gesicherte Stand ist beschädigt")
        doc.pop("wifi", None)
        return self._apply_doc(doc, None)

    def _apply_cameras(self, cams):
        with self.cams.lock:
            old = {c["key"]: c for c in self.cams.cams}
            new = []
            for c in cams:
                cam = {"id": old[c["key"]]["id"] if c["key"] in old else secrets.token_hex(4), "name": c["name"], "key": c["key"], "role": c["role"]}
                if c.get("iface") and self.cams.host_for_iface(c["iface"]) is not None:
                    cam["iface"] = c["iface"]
                new.append(cam)
            gone = len([k for k in old if k not in {c["key"] for c in cams}])
            self.cams.cams = new
            self.cams.save()
        return "%d Kameras eingespielt%s" % (len(new), (", %d nicht in der Sicherung entfernt" % gone) if gone else "")

    def _apply_pipeline(self, req):
        self.pipeline.set(dict(req), [c["key"] for c in self.cams.cams])
        return "Bildaufbau eingespielt"

    def _apply_srtla(self, new):
        valid = [o["iface"] for o in iface_ips()]
        with self.srtla.lock:
            old = list(self.srtla.data["servers"])
            servers = []
            for s in new["servers"]:
                same = next((o for o in old if (o["name"], o["host"], o["port"]) == (s["name"], s["host"], s["port"])), None)
                servers.append({"id": same["id"] if same else secrets.token_hex(4), "name": s["name"], "host": s["host"], "port": s["port"],
                                "streamid": s["streamid"] or ((same or {}).get("streamid", ""))})
            self.srtla.data["servers"] = servers
            self.srtla.data["selected"] = servers[new["selected"]]["id"] if servers and new["selected"] is not None else (servers[0]["id"] if servers else None)
            st = dict(new["settings"])
            ups = [u for u in st["uplinks"] if u in valid]
            st["uplinks"] = ups or list(self.srtla.data["settings"].get("uplinks", []))
            self.srtla.data["settings"] = st
            self.srtla.save()
        skipped = len(new["settings"]["uplinks"]) - len(ups)
        return "%d SRTLA-Server eingespielt%s" % (len(servers), (", %d Netze zum Senden gibt es hier nicht (ausgelassen)" % skipped) if skipped else "")

    def _apply_autostart(self, d):
        self.autostart.set_enabled(d["enabled"])
        return "Automatischer Start: " + ("an" if d["enabled"] else "aus")

    def _apply_names(self, names):
        for k, v in names.items():
            self.names.set(k, v)
        return "%d Namen eingespielt" % len(names)

    def _apply_dji(self, cams):
        done, problems = 0, []
        for c in cams:
            try:
                self.djisvc.command({"cmd": "add", "addr": c["addr"], "name": c["name"], "model": c["model"], "kind": c["kind"]})
                upd = {"cmd": "update"}
                upd.update({k: v for k, v in c.items() if k not in ("kind", "model")})
                self.djisvc.command(upd)
                done += 1
            except (ValueError, RuntimeError) as e:
                problems.append("%s: %s" % (c["name"], e))
                if isinstance(e, RuntimeError):
                    break
        if problems and not done:
            raise RuntimeError("; ".join(problems))
        return "%d DJI-Kameras eingespielt (bitte einmal verbinden)" % done + ((", Probleme: " + "; ".join(problems)) if problems else "")

    def _apply_hdmi(self, d):
        if self.hdmi is None:
            raise RuntimeError("Der HDMI-Dienst ist hier nicht verfügbar")
        self.hdmi.set(dict(d))
        return "HDMI-Einstellungen eingespielt"

    def _apply_twitch(self, d):
        if self.twitch is None:
            raise RuntimeError("Die Akku-Warnung ist hier nicht verfügbar")
        self.twitch.set(dict({k: d[k] for k in ("channel", "login", "threshold", "message", "only_live")}, enabled=False))      # erst die Werte, dann ggf. einschalten
        if not d["enabled"]:
            return "Akku-Warnung: Einstellungen eingespielt (aus)"
        try:
            self.twitch.set({"enabled": True})
        except ValueError:
            return "Akku-Warnung: eingespielt, bleibt aber ausgeschaltet (Token oder Twitch-Anmeldung fehlt auf dieser Box)"
        return "Akku-Warnung: eingespielt (an)"

    def _apply_camnet(self, d):
        if self.netchoice is None:
            raise RuntimeError("Das Netzwerk für Kameras ist hier nicht verfügbar")
        try:
            self.netchoice.select(d["iface"])
        except ValueError:
            return "Netzwerk für Kameras: „%s“ gibt es hier nicht (ausgelassen)" % d["iface"]
        return "Netzwerk für Kameras eingespielt"

    def _apply_hotspots(self, hs):
        return self.wifi.hotspot_import(hs)

    def _apply_wifi(self, d):
        return self.wifi.import_saved(d["networks"])


def client_wifi_list():
    """WLAN-Schnittstellen, in denen die Box nur Gast ist (verbunden mit einem fremden Netz, nicht der eigene Hotspot): [{"iface", "ip"}].
    NetworkManager: Typ wifi, verbunden, Modus der Verbindung nicht "ap". Bei jedem Fehler leer (dann wird nichts gesperrt)."""
    def run(args):
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=4).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    out = []
    for line in run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"]).splitlines():
        parts = line.replace("\\:", "\x00").split(":")
        if len(parts) < 4 or parts[1] != "wifi" or parts[2] != "connected":
            continue
        conn = parts[3].replace("\x00", ":")
        if run(["nmcli", "-g", "802-11-wireless.mode", "connection", "show", conn]) == "ap":
            continue                                                    # der eigene Hotspot der Box
        ip = run(["nmcli", "-g", "IP4.ADDRESS", "dev", "show", parts[0]]).split("/")[0].split("\n")[0]
        if ip:
            out.append({"iface": parts[0], "ip": ip})
    return out


class UiAccess:
    """Schalter "Oberfläche über fremde WLANs sperren" (Issue #25): Die Oberfläche läuft unverschlüsselt (HTTP). In einem WLAN, in dem die Box nur
    Gast ist, liest dort jeder mit. Ist der Schalter an, werden Verbindungen auf der Adresse eines solchen WLANs ohne Antwort getrennt. Ethernet,
    der eigene Hotspot, USB und Tailscale bleiben erreichbar. Aus der Aussperrung heraus: per Ethernet/Tailscale ausschalten oder die Datei
    ui-access.json im Zustandsordner löschen. Standard: aus (sonst könnte sich sperren, wer die Box nur per WLAN erreicht)."""
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
        """Soll eine Verbindung auf dieser eigenen Adresse abgewiesen werden?"""
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


def source_allowed(ip):
    """Darf diese Adresse die Oberfläche erreichen? Nur eigene und private Netze: Loopback (Tailscale-Proxy), private Adressbereiche, Link-Local,
    Carrier-Grade-NAT (100.64.0.0/10, dort liegt auch Tailscale) und IPv6-ULA. Eine öffentliche Quelladresse heißt: die Oberfläche hängt
    versehentlich im Internet (z. B. Portfreigabe, Modem mit öffentlicher Adresse); die KONZEPT.md sagt "nur privat erreichbar" (Issue #25)."""
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return bool(a.is_loopback or a.is_private or a.is_link_local or (a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10")))


class LimitedHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer mit Grenzen (Issue #25): höchstens MAX_CONN Verbindungen zugleich, davon höchstens PER_IP je Absender (ein einzelner
    Client mit lauter halb offenen Anfragen sperrt so nicht alle anderen aus), und keine Quelladressen aus dem öffentlichen Internet."""
    daemon_threads = True
    request_queue_size = 64
    MAX_CONN = 64
    PER_IP = 16
    allow_public = False
    ui_access = None

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._conn_lock = threading.Lock()
        self._conns = {}
        self._senders = {}
        self._total = 0

    LOOPBACK_CONN = 48        # vom Tailscale-Proxy (Serve/Funnel): davor nur diese Obergrenze, danach zählt der echte Absender aus X-Forwarded-For

    def verify_request(self, request, client_address):
        ip = client_address[0]
        if not self.allow_public and not source_allowed(ip):
            return False
        loop = self.is_loopback(ip)
        ua = self.ui_access
        if ua is not None and ua.block:                                   # über fremde WLANs (Gast-WLAN der Box) nicht erreichbar
            try:
                if ua.refuses(request.getsockname()[0]):
                    return False
            except (OSError, AttributeError):
                pass
        with self._conn_lock:
            if self._total >= self.MAX_CONN or (self._conns.get(ip, 0) >= (self.LOOPBACK_CONN if loop else self.PER_IP)):
                return False
            self._conns[ip] = self._conns.get(ip, 0) + 1
            self._total += 1
        return True

    @staticmethod
    def is_loopback(ip):
        return ip in ("127.0.0.1", "::1")

    def claim_sender(self, ip):
        """Hinter dem Proxy: den echten Absender zählen (höchstens PER_IP offene Verbindungen). Wahr, wenn noch Platz ist."""
        with self._conn_lock:
            if self._senders.get(ip, 0) >= self.PER_IP:
                return False
            self._senders[ip] = self._senders.get(ip, 0) + 1
            return True

    def release_sender(self, ip):
        with self._conn_lock:
            n = self._senders.get(ip, 1) - 1
            if n > 0:
                self._senders[ip] = n
            else:
                self._senders.pop(ip, None)

    def handle_error(self, request, client_address):
        """Ein Gegenüber, das die Verbindung abbricht oder zu langsam ist (Issue #25), ist kein Fehler des Dienstes: kein Traceback im Journal
        (sonst wird das Protokoll bei vielen abgeschnittenen Anfragen unlesbar)."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError, socket.timeout)) or (isinstance(exc, OSError) and exc.errno in (9, 32, 54, 104)):
            return
        super().handle_error(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            ip = client_address[0]
            with self._conn_lock:
                self._total -= 1
                n = self._conns.get(ip, 1) - 1
                if n > 0:
                    self._conns[ip] = n
                else:
                    self._conns.pop(ip, None)


def scene_swap(pipeline, cams, send, with_key=None):
    """Hauptbild gegen ein kleines Bild tauschen (Oberfläche und Controller-Tasten). Ohne Angabe das erste kleine Bild.
    Gibt die Antwort der Schnittstelle zurück oder löst ValueError mit einem lesbaren Text aus."""
    if with_key is not None and (not isinstance(with_key, str) or not KEY_RE.match(with_key)):
        raise ValueError("Kamera unbekannt")
    target = with_key or pipeline.cfg.get("pip")
    if target and any(x["key"] == target and x.get("state") == "offline" for x in cams.listing("")):
        raise ValueError("Diese Kamera ist nicht verbunden und lässt sich nicht zum Hauptbild machen.")
    shown = pipeline.swap_main_pip(with_key)
    if send.always_live() or send.swap_live():
        note = "Getauscht, ohne Unterbrechung."
        if shown:                      # das kleine Bild der bisherigen Hauptkamera war ausgeblendet gespeichert und ist jetzt sichtbar
            hide, aud = PipelineStore.view_values(pipeline.cfg)
            cur = send.view_state()
            if not (send.view_live() and send.apply_view(hide, aud, bool(cur and cur["mute"]))):
                note += " Das kleine Bild erscheint beim nächsten Start der Sendung."
        return {"ok": True, "restarted": False, "note": note}
    restarted, note = send.restart_if_live()
    return {"ok": True, "restarted": restarted, "note": note or "Getauscht."}


def controller_action(fn, pipeline, cams, send):
    """Funktion einer Controller-Taste ausführen. Bild-Funktionen tauschen immer das Hauptbild: „Kleines Bild N“ holt die Kamera,
    die gerade dort ist, ins Hauptbild (die bisherige Hauptkamera nimmt ihren Platz ein; nochmal drücken tauscht zurück); „Hauptbild“
    holt die Kamera mit der Rolle Hauptbild der Kameraliste zurück. Ton: stumm schalten (an/aus) und zur nächsten Tonquelle."""
    cfg = pipeline.cfg
    if fn in ("pip1", "pip2", "pip3"):
        key = cfg.get({"pip1": "pip", "pip2": "pip2", "pip3": "pip3"}[fn])
        if not key or cfg.get("type") != "pip":
            return
        scene_swap(pipeline, cams, send, key)
    elif fn == "main":
        want = next((c["key"] for c in cams.cams if c.get("role") == "main"), None)
        if want and want != cfg.get("main") and cfg.get("type") == "pip":
            scene_swap(pipeline, cams, send, want)
    elif fn == "mute":
        f = send.footer()
        send.change_view(mute=not bool(f and f["audio"]["mute"]))
    elif fn == "audio_next":
        f = send.footer()
        if f:
            send.change_view(audio=f["audio"]["next"])


class Handler(BaseHTTPRequestHandler):
    timeout = 15              # eine Verbindung, die so lange nichts sendet, wird beendet (Issue #25: halb offene Anfragen blieben ewig offen)
    BODY_SECONDS = 15.0       # so lange darf der Inhalt einer Anfrage insgesamt brauchen (nicht nur je Teilstück)
    REQUEST_SECONDS = 15.0    # Gesamtfrist für Kopfzeilen und Inhalt zusammen; danach wird die Verbindung getrennt (ein Byte alle paar Sekunden hielt sie offen)
    _timer = None
    _claimed = None

    def setup(self):
        super().setup()
        self._timer = threading.Timer(self.REQUEST_SECONDS, self._cut)
        self._timer.daemon = True
        self._timer.start()

    def _cut(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass

    def request_read(self):
        """Die Anfrage ist vollständig gelesen: Gesamtfrist beenden (Antworten schreiben hat seine eigene Zeitgrenze je Teilstück)."""
        t = self._timer
        if t is not None:
            t.cancel()

    def finish(self):
        self.request_read()
        if self._claimed is not None:
            try:
                self.server.release_sender(self._claimed)
            except AttributeError:
                pass
            self._claimed = None
        super().finish()

    def parse_request(self):
        ok = super().parse_request()
        if ok and self.client_address[0] in ("127.0.0.1", "::1") and hasattr(self.server, "claim_sender"):
            real = self.ip()                                           # Anfrage vom Proxy: den echten Absender zählen
            if real not in ("127.0.0.1", "::1"):
                if not self.server.claim_sender(real):
                    self.send_error(429, "Zu viele Verbindungen")
                    return False
                self._claimed = real
        return ok
    sampler = None
    cams = None
    auth = None
    updates = None
    djisvc = None
    netchoice = None
    names = None
    ckeys = None
    srtla = None
    pipeline = None
    send = None
    preview = None
    twitch = None
    hdmi = None
    uiaccess = None

    def log_message(self, *a):
        pass

    def host(self):
        return (self.headers.get("Host") or "box").split(":")[0]

    def ip(self):
        """Adresse des Gegenübers. Kommt die Anfrage vom Tailscale-Proxy auf dieser Box (Serve/Funnel), zählt die Adresse, die der
        Proxy als letzte in X-Forwarded-For angehängt hat: So sperren fehlgeschlagene Anmeldungen einzelne Absender und nicht alle."""
        peer = self.client_address[0]
        if peer in ("127.0.0.1", "::1"):
            last = (self.headers.get("X-Forwarded-For", "") or "").split(",")[-1].strip()
            try:
                ipaddress.ip_address(last)
                return last
            except ValueError:
                pass
        return peer

    def local_ip(self):
        """Eigene Adresse, auf der diese Verbindung ankam (welches Netz der Box)."""
        try:
            return self.connection.getsockname()[0]
        except (OSError, AttributeError):
            return None

    def token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "pb_session":
                return v
        return ""

    def authed(self):
        return self.auth.valid(self.token())

    def read_json(self, limit=4096):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:
            raise ValueError("ungültige Länge")
        if n > limit:
            raise ValueError("Die Anfrage ist zu groß")
        data = self.read_body(n)
        self.request_read()
        out = json.loads(data or b"{}")
        if not isinstance(out, dict):
            raise ValueError("Ungültige Anfrage")                 # alle Routen erwarten ein Objekt
        return out

    def read_body(self, n):
        """Liest n Bytes, insgesamt höchstens BODY_SECONDS lang (ein Absender, der alle paar Sekunden ein Byte schickt, hält sonst die Verbindung offen)."""
        end = time.monotonic() + self.BODY_SECONDS
        buf = b""
        while len(buf) < n:
            left = end - time.monotonic()
            if left <= 0:
                raise ValueError("Die Anfrage kam zu langsam")
            conn = getattr(self, "connection", None)
            if conn is None:                                      # ohne Verbindung (Tests mit nachgestellter Anfrage): einfach lesen
                return self.rfile.read(n)
            conn.settimeout(left)
            try:
                chunk = self.rfile.read1(n - len(buf))
            except OSError:
                raise ValueError("Die Anfrage kam zu langsam")
            if not chunk:
                raise ValueError("Die Anfrage ist unvollständig")
            buf += chunk
        return buf

    def preview_stream(self):
        """Motion-JPEG des gesendeten Bildes (Issue #52): multipart/x-mixed-replace, durchgereicht vom Vorschau-Dienst, bis der Browser geht, die Zeit um ist oder die Sendung endet."""
        pv = self.preview
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if not pv:
            return self.reply(503, {"error": "missing"})
        if not self.send._active():
            return self.reply(503, {"error": "off"})
        try:
            fmt = (q.get("fmt") or ["mp4"])[0]
            fmt = fmt if fmt in pipbox_preview.FORMATS else "mp4"
            up, head = pv.open((q.get("fps") or [30])[0], (q.get("w") or [640])[0], (q.get("long") or [""])[0] == "1", fmt)
        except OSError:
            return self.reply(503, {"error": "missing"})
        if up is None:
            why = head.get("error", "capture")
            return self.reply(429 if why == "busy" else 503, {"error": why})
        started = False
        self.close_connection = True
        lip = self.local_ip() or ""
        try:
            up.settimeout(20)
            while True:
                data = up.recv(65536)
                if not data:
                    break
                if not started:
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4" if fmt == "mp4" else "multipart/x-mixed-replace;boundary=" + pipbox_preview.BOUNDARY)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("X-Frame-Options", "DENY")
                    self.end_headers()
                    started = True
                self.wfile.write(data)
                self.wfile.flush()
                pipbox_preview.TRAFFIC.add(lip, len(data))               # für die Anzeige des Uploads (siehe Sampler.net_bytes)
        except OSError:
            pass                                       # Browser weg oder Dienst still: Verbindung zum Dienst schließen beendet die Vorschau dort
        finally:
            try:
                up.close()
            except OSError:
                pass
        if not started:
            self.reply(503, {"error": "kein-bild"})

    def send_bytes(self, code, body, ctype, cookie=None, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def reply(self, code, obj, cookie=None):
        self.send_bytes(code, json.dumps(obj).encode(), "application/json", cookie)

    def cookie(self, tok, max_age):
        flags = "HttpOnly; SameSite=Strict; Path=/"
        if self.headers.get("X-Forwarded-Proto") == "https":
            flags += "; Secure"
        return f"pb_session={tok}; Max-Age={max_age}; {flags}"

    I18N_RE = re.compile(r"^/i18n/([a-z]{2,3}|languages)\.json$")

    def i18n_file(self, path):
        """Übersetzungsdateien der Seite (Issue #24): /i18n.js und /i18n/<Sprache>.json aus web/. Öffentlich (die Anmeldeseite braucht sie), keine Geheimnisse."""
        if path == "/i18n.js":
            name, ctype = "i18n.js", "application/javascript; charset=utf-8"
        else:
            m = self.I18N_RE.match(path)
            if not m:
                return False
            name, ctype = "i18n/%s.json" % m.group(1), "application/json; charset=utf-8"
        body = read(os.path.join(WEB_DIR, name), None)
        if body is None:
            self.reply(404, {"error": "not found"})
        else:
            self.send_bytes(200, body.encode("utf-8"), ctype)
        return True

    def page(self, name):
        body = read(os.path.join(WEB_DIR, name), "")
        self.send_bytes(200, body.encode(), "text/html; charset=utf-8")

    def conn_labels(self):
        """Namen hinter den Schnittstellen (Status, Sendewege): eigene Namen der Verbindungen, bei WLAN-Karten ohne eigenen Namen der Name des Sticks."""
        base = dict(self.wifi.labels()) if self.wifi else {}
        base.update(self.names.conn_names() if self.names else {})
        return base

    def do_GET(self):
        self.request_read()
        path = self.path.split("?")[0]
        if (path == "/i18n.js" or path.startswith("/i18n/")) and self.i18n_file(path):
            return
        if path in ("/", "/index.html"):
            return self.page("index.html" if self.authed() else "login.html")
        if path == "/api/auth":
            out = {"configured": self.auth.configured, "mode": self.auth.mode, "authed": self.authed()}
            if self.auth.mode == "demo":
                out["demo_password"] = Auth.DEMO_PASSWORD     # nur in der Vorschau: die Anmeldeseite füllt es vor
            return self.reply(200, out)
        if path == "/api/swprogress":
            return self.reply(200, self.swupdate.progress_public())
        if not self.authed():
            return self.reply(401, {"error": "nicht angemeldet"})
        if path == "/api/metrics":
            m = self.sampler.sample()
            try:
                m["alerts"] = list(m.get("alerts") or []) + self.usbwatch.alerts()
            except Exception:
                pass
            m["cameras"] = self.cams.listing(self.host(), self.djisvc.host_for_key)
            try:
                hs = self.hdmi.status() if self.hdmi else None
                m["alerts"] = list(m.get("alerts") or []) + self.outages.alerts(m["cameras"], hs)
            except Exception:
                pass
            extras = self.djisvc.camera_extras()
            for c in m["cameras"]:
                c.update(extras.get(c["key"], {}))             # Akkustand der DJI-Kameras (Status, Kameras)
            m["uplinks"] = uplink_states(((self.srtla.data or {}).get("settings") or {}).get("uplinks") or [])
            m["conn_names"] = self.conn_labels()
            pic = self.send.picture()
            for c in m["cameras"]:
                p = pic.get(c["key"]) if pic else None
                c["pic"], c["pic_wait"] = (p[0], p[1]) if p else (None, 0)
            for c in m["cameras"]:
                if c.get("state") == "live" and not c.get("fps"):
                    f = self.djisvc.fps_for_key(c["key"])
                    if f:
                        c["fps"], c["fps_set"] = float(f), True      # eingestellt, nicht gemessen
            return self.reply(200, m)
        if path == "/api/preview":
            if not self.preview:
                return self.reply(200, {"available": False, "why": "missing"})
            if not self.send._active():
                return self.reply(200, {"available": False, "why": "off"})            # ohne Sendung gar nicht erst den Dienst wecken
            return self.reply(200, self.preview.status())
        if path == "/api/preview/stream":
            return self.preview_stream()
        if path == "/api/controller-keys":
            return self.reply(200, self.ckeys.snapshot())
        if path == "/api/logmode":
            return self.reply(200, self.logmode.status())
        if path == "/api/logs":
            return self.reply(200, self.logbundle.status())
        if path == "/api/settings":
            return self.reply(200, {"has_backup": os.path.isfile(self.transfer.backup_path), "sending": bool(self.send._active())})
        if path == "/api/developer":
            return self.reply(200, self.developer.status())
        if path == "/api/developer/password":
            try:
                return self.reply(200, self.developer.password())
            except ValueError as e:
                return self.reply(404, {"error": str(e)})
        if path == "/api/logs/file":
            body = self.logbundle.content()
            if body is None:
                return self.reply(404, {"error": "Es liegt noch keine fertige Protokolldatei vor"})
            return self.send_bytes(200, body, "text/plain; charset=utf-8",
                                   headers={"Content-Disposition": 'attachment; filename="irl4you-protokolle.txt"'})
        if path == "/api/power":
            return self.reply(200, self.power.status())
        if path == "/api/wifi":
            return self.reply(200, self.wifi.status())
        if path == "/api/wifi/hotspot":
            q = urllib.parse.parse_qs((self.path.split("?", 1) + [""])[1])
            try:
                return self.reply(200, self.wifi.hotspot_secret((q.get("iface") or [""])[0]))
            except ValueError as e:
                return self.reply(404, {"error": str(e)})
        if path == "/api/remote":
            return self.reply(200, self.remote.status())
        if path == "/api/swupdate":
            q = (self.path.split("?", 1) + [""])[1]
            return self.reply(200, self.swupdate.status(force="check=1" in q, fresh="fresh=1" in q))
        if path == "/api/update":
            try:
                self.updates.auto_check(present=True)       # Wer die Seite öffnet, soll gleich wissen, ob es Updates gibt (einmal je Start, still)
            except Exception as e:
                print("auto_check (Anmeldung):", e)
            return self.reply(200, self.updates.status())
        if path == "/api/dji":
            st = label_bluetooth(self.djisvc.status(), self.names)
            return self.reply(200, st)
        if path == "/api/uiaccess":
            return self.reply(200, self.uiaccess.status(self.local_ip()))
        if path == "/api/twitch":
            return self.reply(200, self.twitch.status())
        if path == "/api/twitch/login":
            return self.reply(200, self.twitchlogin.status())
        if path == "/api/twitch/bot":
            return self.reply(200, self.twitchbot.status())
        if path == "/api/chat":
            qs = urllib.parse.parse_qs((self.path.split("?", 1) + [""])[1])
            try:
                since = max(0, int((qs.get("since") or ["0"])[0]))
            except ValueError:
                since = 0
            out = self.chatreader.poll(since)
            out["account"] = self.twitchlogin.status()
            out["events"] = self.chatevents.status() if getattr(self, "chatevents", None) else {"state": "aus", "types": [], "error": ""}
            return self.reply(200, out)
        if path == "/api/hdmi":
            return self.reply(200, self.hdmi.status())
        if path == "/api/network":
            return self.reply(200, self.netchoice.status())
        if path == "/api/srtla":
            return self.reply(200, {**self.srtla.public(), "interfaces": iface_ips(), "conn_names": self.conn_labels()})
        if path == "/api/pipeline":
            st = self.pipeline.status(self.cams.listing(""))
            st["live"] = {"main": self.send.delay_live(), "pips": self.send.delay_live_pips()}
            return self.reply(200, st)
        if path == "/api/send":
            out = self.send.status()
            out["autostart"] = self.autostart.status()
            return self.reply(200, out)
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            d = self.read_json(SettingsTransfer.MAX_BODY if path.startswith("/api/settings/") and self.authed() else 4096)
            if path == "/api/setup":
                self.auth.set_password(d.get("code"), d.get("password"), self.ip())
                rem = d.get("remember") is True
                tok = self.auth.login(d.get("password"), self.ip(), rem)
                return self.reply(200, {"ok": True}, self.cookie(tok, REMEMBER_SECONDS if rem else SESSION_SECONDS))
            if path == "/api/login":
                rem = d.get("remember") is True
                tok = self.auth.login(d.get("password"), self.ip(), rem)
                return self.reply(200, {"ok": True}, self.cookie(tok, REMEMBER_SECONDS if rem else SESSION_SECONDS))
            if path == "/api/logout":
                self.auth.logout(self.token())
                return self.reply(200, {"ok": True}, self.cookie("", 0))
            if not self.authed():
                return self.reply(401, {"error": "nicht angemeldet"})
            if path == "/api/network":
                self.netchoice.select(d.get("iface"))
                return self.reply(200, {"ok": True})
            if path == "/api/send":
                self.send.request(d.get("action"), d.get("confirm") is True)
                if d.get("action") in ("stop", "start"):
                    self.autostart.cancel("Sendung wurde von Hand gestartet oder beendet")
                return self.reply(200, {"ok": True})
            if path == "/api/autostart":
                self.autostart.set_enabled(d.get("enabled"))
                return self.reply(200, self.autostart.status())
            if path == "/api/power":
                self.power.request(d.get("action"), d.get("confirm") is True)
                return self.reply(200, {"ok": True})
            if path == "/api/logmode":
                self.logmode.request(d.get("mode"))
                return self.reply(200, {"ok": True})
            if path == "/api/developer":
                self.developer.request(d.get("action"), d.get("confirm") is True)
                return self.reply(200, self.developer.status())
            if path == "/api/logs":
                if d.get("action") != "collect":
                    raise ValueError("Unbekannte Aktion")
                self.logbundle.request()
                return self.reply(200, self.logbundle.status())
            if path == "/api/wifi":
                self.wifi.request(d)
                return self.reply(200, {"ok": True})
            if path == "/api/controller-keys":
                self.ckeys.set_map(d.get("addr"), d.get("code"), d.get("fn"))
                return self.reply(200, {"ok": True})
            if path == "/api/devname":
                if self.names is None:
                    raise ValueError("Namen sind hier nicht verfügbar")
                self.names.set(d.get("key"), d.get("name", ""))
                return self.reply(200, {"ok": True})
            if path == "/api/remote":
                self.remote.request(d.get("action"), d.get("confirm") is True, d.get("public") is True)
                return self.reply(200, {"ok": True})
            if path.startswith("/api/settings/"):
                pw = d.get("password")
                if pw is not None and not isinstance(pw, str):
                    raise ValueError("Passwort ungültig")
                if path == "/api/settings/export":
                    out = self.transfer.export(d.get("secrets") is True, pw or None, d.get("wifi") is not False)
                    return self.reply(200, dict(out, ok=True))
                if path == "/api/settings/preview":
                    return self.reply(200, self.transfer.preview(d.get("document"), pw or None))
                if path == "/api/settings/import":
                    secs = d.get("sections")
                    if secs is not None and (not isinstance(secs, list) or any(not isinstance(x, str) for x in secs)):
                        raise ValueError("Auswahl ungültig")
                    return self.reply(200, self.transfer.apply(d.get("document"), pw or None, secs))
                if path == "/api/settings/restore":
                    return self.reply(200, self.transfer.restore())
                return self.reply(404, {"error": "not found"})
            if path == "/api/swupdate":
                self.swupdate.request(d.get("action"), d.get("confirm") is True, d.get("version"), d.get("older") is True)
                return self.reply(200, {"ok": True})
            if path == "/api/pipeline/view":
                if "visible" not in d and "audio" not in d and "mute" not in d:
                    raise ValueError("Nichts zu ändern")
                return self.reply(200, dict(self.send.change_view(d.get("visible"), d.get("audio"), d.get("mute")), ok=True))
            if path == "/api/pipeline/active":
                self.pipeline.set_active(d.get("key"), d.get("active"))
                return self.reply(200, {"ok": True})
            if path == "/api/pipeline/swap":
                return self.reply(200, scene_swap(self.pipeline, self.cams, self.send, d.get("with")))
            if path == "/api/pipeline":
                before = dict(self.pipeline.cfg)
                before["styles"] = clean_styles(before.get("styles"))        # eine unveränderte Einstellung ohne Stile gilt nicht als Änderung
                self.pipeline.set(d, [c["key"] for c in self.cams.cams])
                restarted, note = (False, "")
                if self.pipeline.cfg != before and self.send.always_live() and always_compatible(before, self.pipeline.cfg):
                    # "alle Kameras immer bereit": der Sende-Dienst liest die Einstellung und übernimmt Zuordnung und Verzögerung selbst; Ansicht und Ton stellt pbctl
                    hide, aud = PipelineStore.view_values(self.pipeline.cfg)
                    cur = self.send.view_state()
                    if (hide, aud) != PipelineStore.view_values(before):
                        self.send.apply_view(hide, aud, bool(cur and cur["mute"]))
                    return self.reply(200, {"ok": True, "restarted": False, "note": "Gespeichert. Die Änderung gilt sofort, ohne Neustart."})
                if self.pipeline.cfg != before and view_only_change(before, self.pipeline.cfg):
                    # nur "Bild einblenden" und/oder die Tonquelle geändert: im Betrieb ohne Neustart übernehmen, wenn die Sendekette das kann
                    hide, aud = PipelineStore.view_values(self.pipeline.cfg)
                    audio_changed = before.get("audio") != self.pipeline.cfg.get("audio")
                    cur = self.send.view_state()
                    if self.send.view_live() and (not audio_changed or self.send.audio_live()) and \
                            self.send.apply_view(hide, aud, bool(cur and cur["mute"])):
                        return self.reply(200, {"ok": True, "restarted": False, "note": "Gespeichert. Die Änderung wird live übernommen, ohne Neustart."})
                    if self.send.view_degraded():
                        print("Ansicht im Notbetrieb gespeichert, kein Neustart", flush=True)
                        return self.reply(200, {"ok": True, "restarted": False, "note": "Gespeichert. Gilt, sobald alle Kameras wieder da sind (Notbetrieb: ohne Neustart)."})
                if self.pipeline.cfg != before:
                    only_delay = {k: v for k, v in before.items() if k not in DELAY_KEYS} == \
                                 {k: v for k, v in self.pipeline.cfg.items() if k not in DELAY_KEYS}
                    pips_changed = any(before.get(k) != self.pipeline.cfg.get(k) for k in DELAY_KEYS[1:])
                    live_ok = self.send.delay_live_pips() if pips_changed else self.send.delay_live()
                    if only_delay and live_ok:
                        note = "Gespeichert. Die Verzögerung wird live übernommen, ohne Neustart."
                    else:
                        restarted, note = self.send.restart_if_live()
                return self.reply(200, {"ok": True, "restarted": restarted, "note": note})
            if path == "/api/srtla":
                return self.reply(200, {"id": self.srtla.add(d)})
            if path == "/api/srtla/select":
                self.srtla.select(d.get("id"))
                return self.reply(200, {"ok": True})
            if path == "/api/srtla/settings":
                self.srtla.set_settings(d, [o["iface"] for o in iface_ips()] or ["eth0", "eth1"])
                return self.reply(200, {"ok": True})
            m2 = re.match(r"^/api/srtla/([0-9a-f]{8})$", path)
            if m2:
                try:
                    self.srtla.update(m2.group(1), d)
                    return self.reply(200, {"ok": True})
                except KeyError:
                    return self.reply(404, {"error": "nicht gefunden"})
            if path == "/api/dji/cmd":
                return self.reply(200, self.djisvc.command(d))
            if path == "/api/alerts/dismiss":
                if d.get("kind") in ("cam", "hdmi"):
                    return self.reply(200, {"ok": bool(self.outages.dismiss(d.get("kind"), d.get("t")))})
                return self.reply(200, {"ok": bool(self.usbwatch.dismiss(d.get("t")))})
            if path == "/api/uiaccess":
                return self.reply(200, self.uiaccess.set(d.get("block_client_wifi"), self.local_ip()))
            if path == "/api/twitch/login":
                act = d.get("action")
                if act == "start":
                    return self.reply(200, self.twitchlogin.start(mod=d.get("mod") is True, events=d.get("events") is True))
                if act == "cancel":
                    return self.reply(200, self.twitchlogin.cancel())
                if act == "mod" and isinstance(d.get("on"), bool):
                    return self.reply(200, self.twitchlogin.set_mod(d["on"]))
                return self.reply(400, {"error": "Ungültige Anfrage"})
            if path == "/api/twitch/logout":
                return self.reply(200, self.twitchlogin.logout())
            if path == "/api/twitch/bot":
                act = d.get("action")
                if act == "start":
                    return self.reply(200, self.twitchbot.start())
                if act == "cancel":
                    return self.reply(200, self.twitchbot.cancel())
                if act == "logout":
                    r = self.twitchbot.logout()
                    with self.twitch.lock:
                        self.twitch.tries.clear()                              # Versuche neu starten; ohne Bot gilt wieder das Hauptkonto oder der Token von Hand
                    return self.reply(200, r)
                return self.reply(400, {"error": "Ungültige Anfrage"})
            if path == "/api/chat/mod":
                return self.reply(200, self.chatmod.do(d))
            if path == "/api/chat/send":
                return self.reply(200, self.chatsender.say(d.get("text")))
            if path == "/api/twitch":
                return self.reply(200, self.twitch.save(d))
            if path == "/api/twitch/test":
                return self.reply(200, self.twitch.test())
            if path == "/api/hdmi":
                return self.reply(200, self.hdmi.set(d))
            if path == "/api/update":
                self.updates.request(d.get("mode"), d.get("confirm") is True)
                return self.reply(200, {"ok": True})
            m = re.match(r"^/api/cameras/([0-9a-f]{8})$", path)
            if m:
                try:
                    if d.get("swap_with") is not None:
                        if not isinstance(d["swap_with"], str):
                            raise ValueError("Kamera ungültig")
                        return self.reply(200, self.cams.swap(m.group(1), d["swap_with"]))
                    if d.get("iface") is not None:
                        cam = next((c for c in self.cams.cams if c["id"] == m.group(1)), None)
                        if cam and cam["key"].startswith("dji-"):
                            raise ValueError("Die Verbindung einer DJI-Kamera wird in ihrer DJI-Karte gewählt")
                    return self.reply(200, self.cams.update(m.group(1), d.get("name"), d.get("role"), d.get("iface")))
                except KeyError:
                    return self.reply(404, {"error": "nicht gefunden"})
            if path == "/api/cameras":
                cam = self.cams.add(d.get("name"), d.get("key"), d.get("role", "extra"))
                return self.reply(200, cam)
        except PermissionError as e:
            return self.reply(429, {"error": str(e)})
        except RuntimeError as e:
            return self.reply(503, {"error": str(e)})
        except (ValueError, TypeError) as e:
            return self.reply(400, {"error": str(e)})
        self.reply(404, {"error": "not found"})

    def do_DELETE(self):
        self.request_read()
        if not self.authed():
            return self.reply(401, {"error": "nicht angemeldet"})
        m3 = re.match(r"^/api/srtla/([0-9a-f]{8})$", self.path.split("?")[0])
        if m3:
            try:
                self.srtla.remove(m3.group(1))
                return self.reply(200, {"ok": True})
            except KeyError:
                return self.reply(404, {"error": "nicht gefunden"})
        m = re.match(r"^/api/cameras/([0-9a-f]{8})$", self.path.split("?")[0])
        cam = next((c for c in self.cams.cams if c["id"] == m.group(1)), None) if m else None
        if not m or not self.cams.remove(m.group(1)):
            return self.reply(404, {"error": "nicht gefunden"})
        if cam and cam.get("key") == HDMI_KEY and self.hdmi:
            self.hdmi.on_camera_removed()                    # die Kamera "HDMI" ist weg: die Einspeisung ausschalten
        if cam:
            self.cams.forget(cam.get("key"))                 # ein noch sendender Stream kommt nicht von selbst zurück
            if getattr(self, "djisvc", None):
                self.djisvc.remove_for_key(cam.get("key"))   # eine DJI-Kamera auch aus dem Kamera-Dienst nehmen, sonst trägt ihn die Box gleich wieder ein
        self.reply(200, {"ok": True})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--demo", action="store_true", help="Beispielwerte statt /proc")
    ap.add_argument("--allow-public", action="store_true", help=argparse.SUPPRESS)      # auch öffentliche Quelladressen annehmen (sonst nur Loopback und private Netze, Issue #25)
    ap.add_argument("--state", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "state"),
                    help="Ordner für Passwort-Hash und Kameraliste")
    ap.add_argument("--bela-config", default="", help="belaUI config.json: BELABOX-Passwort mitbenutzen")
    ap.add_argument("--rtmp-port", type=int, default=1935, help="RTMP-Port, den Kameras nutzen")
    ap.add_argument("--rtmp-app", default="publish", help="nginx-rtmp-Applikation")
    ap.add_argument("--rtmp-stat-url", default="", help="nginx-rtmp-Statistik (XML), z. B. http://127.0.0.1/stat")
    args = ap.parse_args()
    Handler.updates = Updates(args.state, args.demo)
    if not args.demo:
        threading.Thread(target=Handler.updates.auto_loop, daemon=True).start()
    Handler.djisvc = None  # unten gesetzt, sobald Kameraliste existiert
    Handler.auth = Auth(args.state, args.bela_config or None,
                        demo=args.demo and args.host in ("127.0.0.1", "::1", "localhost"))   # Demo-Passwort nur auf dem eigenen Rechner
    Handler.cams = CameraStore(os.path.join(args.state, "cameras.json"), args.rtmp_app, args.rtmp_stat_url, args.demo)
    Handler.outages = OutageWatch()
    Handler.usbwatch = UsbWatch(os.path.join(args.state, "usb-events.json"), demo=args.demo)
    Handler.sampler = Sampler(args.demo)
    Handler.sampler.sample()  # Startwerte für Ratenberechnung
    Handler.srtla = SrtlaStore(os.path.join(args.state, "srtla.json"))
    Handler.pipeline = PipelineStore(os.path.join(args.state, "pipeline.json"))
    Handler.send = SendControl(args.state, Handler.srtla, Handler.pipeline, Handler.cams, args.demo)
    Handler.preview = pipbox_preview.Client() if pipbox_preview and not args.demo else None          # die Vorschau macht ein eigener Dienst (pipbox-preview.service), der Webserver reicht nur durch
    Handler.swupdate = SwUpdate(args.state, args.demo, Handler.send)
    if not args.demo:
        threading.Thread(target=Handler.swupdate.auto_loop, daemon=True).start()
    Handler.remote = Remote(args.state, args.demo)
    Handler.netchoice = NetChoice(os.path.join(args.state, "camera-net.json"))
    Handler.names = DeviceNames(args.state)
    Handler.wifi = Wifi(args.state, args.demo, Handler.netchoice, Handler.names, Handler.srtla)
    Handler.power = Power(args.state, args.demo, Handler.send)
    Handler.logmode = LogMode(args.state, args.demo)
    Handler.logbundle = LogBundle(args.state, args.demo)
    Handler.developer = Developer(args.state, args.demo, args.bela_config or None)
    Handler.autostart = AutoStart(args.state, Handler.send, args.demo)
    if not args.demo:
        threading.Thread(target=Handler.autostart.run, daemon=True).start()
    Handler.cams.ipfn = Handler.netchoice.ip
    Handler.djisvc = DjiService(args.state, Handler.cams, args.rtmp_app, args.rtmp_port, args.demo)
    Handler.djisvc.pipeline = Handler.pipeline
    Handler.ckeys = controller_keys.ControllerKeys(args.state, lambda fn: controller_action(fn, Handler.pipeline, Handler.cams, Handler.send), demo=args.demo)
    Handler.ckeys.start()                                   # liest die Tasten gekoppelter Controller (Menü "Controller-Tasten")
    Handler.transfer = SettingsTransfer(args.state, Handler.cams, Handler.pipeline, Handler.srtla, Handler.autostart, Handler.names, Handler.djisvc,
                                        Handler.wifi, Handler.send, args.demo)
    def chat_sources():                                           # Quelladressen der gewählten Sendewege, in der Reihenfolge der Einstellung
        ups = list(((Handler.srtla.data or {}).get("settings") or {}).get("uplinks") or [])
        ips = {x["iface"]: x["ip"] for x in iface_ips() if x.get("ip")}
        return [ips[u] for u in ups if u in ips]
    Handler.chatpaths = ChatPaths(chat_sources)
    Handler.transfer.netchoice = Handler.netchoice
    Handler.twitch = TwitchNotifier(TwitchStore(os.path.join(args.state, "twitch.json")), Handler.djisvc, Handler.cams, Handler.send, chat=TwitchChat(paths=Handler.chatpaths), demo=args.demo)
    Handler.twitchlogin = TwitchLogin(os.path.join(args.state, "twitch-login.json"), demo=args.demo, paths=Handler.chatpaths)
    Handler.twitch.store.account = Handler.twitchlogin                                # angemeldetes Konto ersetzt Bot-Konto und Token von Hand
    Handler.twitchbot = TwitchBotLogin(os.path.join(args.state, "twitch-bot-login.json"), demo=args.demo, paths=Handler.chatpaths)
    Handler.twitch.store.bot = Handler.twitchbot                                      # angemeldetes Bot-Konto schreibt die Akku-Meldung (nur sie)
    if not args.demo:
        threading.Thread(target=Handler.twitchlogin.keep, daemon=True).start()
        threading.Thread(target=Handler.twitchbot.keep, daemon=True).start()
    Handler.chatreader = TwitchReader(Handler.twitch.store, demo=args.demo, paths=Handler.chatpaths)
    Handler.chatmod = TwitchMod(Handler.twitch.store, Handler.twitchlogin, paths=Handler.chatpaths, demo=args.demo, demo_reader=Handler.chatreader if args.demo else None)
    Handler.chathelix = HelixChat(Handler.twitchlogin, Handler.chatmod)
    Handler.chatsender = TwitchSender(Handler.twitch.store, chat=TwitchChat(paths=Handler.chatpaths), demo_reader=Handler.chatreader if args.demo else None, mod=Handler.chatmod, helix=Handler.chathelix)
    Handler.chatevents = TwitchEvents(Handler.twitch.store, Handler.twitchlogin, Handler.chatmod, Handler.chatreader, paths=Handler.chatpaths)
    if not args.demo:
        Handler.chatreader.events = Handler.chatevents       # liest den Kanal der Twitch-Karte (dieselben Einstellungen)
    Handler.hdmi = HdmiService(args.state, Handler.cams, args.demo)
    Handler.transfer.hdmi, Handler.transfer.twitch = Handler.hdmi, Handler.twitch.store
    if not args.demo:
        threading.Thread(target=Handler.twitch.run, daemon=True).start()

    def watcher():
        while True:
            try:
                Handler.cams.auto_add()
            except Exception as e:  # nie den Dienst beenden
                print("auto_add:", e)
            time.sleep(3)
    threading.Thread(target=watcher, daemon=True).start()
    Handler.uiaccess = UiAccess(os.path.join(args.state, "ui-access.json"), demo=args.demo)
    LimitedHTTPServer.ui_access = Handler.uiaccess
    LimitedHTTPServer.allow_public = args.allow_public
    srv = LimitedHTTPServer((args.host, args.port), Handler)
    print(f"PIPBOX auf http://{args.host}:{args.port} (demo={args.demo})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
