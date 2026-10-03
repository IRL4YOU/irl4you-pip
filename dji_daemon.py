#!/usr/bin/env python3
"""Bluetooth-Dienst für DJI-Kameras (pipbox-dji.service).

Läuft getrennt von der Weboberfläche. Grund: Eine DJI-Kamera streamt nur, solange
die Box die Bluetooth-Verbindung hält; startet man die Oberfläche neu, würden sonst
alle Kameras abbrechen. Dieser Dienst hält die Verbindungen, merkt sich, welche
Kameras laufen sollen (dji-active.json), und verbindet sie nach einem Ausfall
selbst wieder (mit wachsender Wartezeit).

Schnittstelle: nur 127.0.0.1, jede Anfrage braucht das Token aus <state>/dji-token
(nur der Benutzer pipbox kann es lesen). Das WLAN-Passwort der Kamera liegt in
dji-active.json (Rechte 0600), niemals im Journal.
"""
import argparse
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dji

PORT = 8793
STAT_URL = "http://127.0.0.1:1936/"   # nginx-rtmp-Statistik
SILENT_LIMIT = 45                    # so lange darf der Stream fehlen, bevor neu verbunden wird
GRACE = 60                           # nach dem Start so lange Zeit lassen, bis der Stream erscheint
BACKOFF = (10, 20, 40, 60)       # Sekunden bis zum nächsten Wiederverbinden
NET_SETTLE = 8                   # nach dem Wiederkehren des Kameranetzes (Router) so lange warten, bis die Kameras ihm beitreten können


def publishing_keys():
    """Schlüssel der Streams, die gerade bei der Box ankommen; None, wenn die Statistik nicht lesbar ist."""
    import re
    import urllib.request
    try:
        x = urllib.request.urlopen(STAT_URL, timeout=3).read().decode()
    except OSError:
        return None
    return {re.search(r"<name>(.*?)</name>", m.group(0)).group(1)
            for m in re.finditer(r"<stream>.*?</stream>", x, re.S) if "<publishing/>" in m.group(0)}


def iface_ip(name):
    """Aktuelle IPv4-Adresse einer Schnittstelle (nur Linux) oder None."""
    import socket
    import struct
    try:
        import fcntl
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            return socket.inet_ntoa(fcntl.ioctl(sk.fileno(), 0x8915,
                                                struct.pack("256s", name[:15].encode()))[20:24])
        finally:
            sk.close()
    except (ImportError, OSError):
        return None


class Daemon:
    def __init__(self, state_dir, dji_obj=None):
        self.state = state_dir
        self.active_path = os.path.join(state_dir, "dji-active.json")
        self.token_path = os.path.join(state_dir, "dji-token")
        self.d = dji_obj or dji.Dji()
        self.lock = threading.Lock()
        self.desired = {}      # Adresse -> Startparameter
        self.tries = {}        # Adresse -> (Versuche, nächster Zeitpunkt)
        self.quiet = {}        # Adresse -> Zeitpunkt, seit dem der Stream fehlt (None = kommt an)
        self.up_since = {}     # Adresse -> Zeitpunkt, seit dem die Sitzung "streaming" meldet
        self.watch_rtmp = True
        self.ipfn = lambda: self._net_ip()    # in Tests ersetzbar
        self.ip_pending = None                # (neue Adresse, Anzahl Prüfungen)
        self.net_down_logged = False
        self.hold_until = 0                  # bis dahin keine neuen Verbindungen (Router fährt noch hoch)
        self.token = self._load_token()
        self._load_active()

    # -- Dateien
    def _load_token(self):
        try:
            with open(self.token_path) as f:
                t = f.read().strip()
            if len(t) >= 32:
                return t
        except OSError:
            pass
        t = secrets.token_urlsafe(32)
        fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(t + "\n")
        return t

    def _load_active(self):
        try:
            with open(self.active_path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.desired = data
        except (OSError, ValueError):
            pass

    def _save_active(self):
        tmp = self.active_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.desired, f)
        os.replace(tmp, self.active_path)

    # -- Befehle
    def net_info(self):
        """Gewähltes Kameranetz und dessen aktuelle Adresse (ip=None: Schnittstelle fehlt, z. B. USB-Router weg)."""
        try:
            with open(os.path.join(self.state, "camera-net.json")) as f:
                name = json.load(f).get("iface")
        except (OSError, ValueError):
            name = None
        if not isinstance(name, str) or not name:
            return {"iface": None, "ip": None}
        return {"iface": name, "ip": iface_ip(name)}

    def status(self):
        st = self.d.status()
        with self.lock:
            st["desired"] = sorted(self.desired)
            # Eine verbundene Kamera sendet keine Bluetooth-Meldung mehr und fehlt deshalb in der letzten Suche.
            # Sie bleibt in der Liste, solange sie laufen soll (sonst fehlen ihre Einstellungen und "Stoppen").
            have = {x["address"].upper() for x in st["devices"]}
            for addr, p in sorted(self.desired.items()):
                if addr.upper() not in have:
                    model = p.get("model", "unknown")
                    st["devices"].append({"address": addr, "name": "", "model": model,
                                          "model_name": dji.MODEL_NAMES.get(model, "DJI-Gerät"), "rssi": None})
        st["net"] = self.net_info()
        return st

    def start(self, p):
        addr = str(p["address"]).upper()
        params = {k: p[k] for k in ("model", "ssid", "password", "url", "res", "fps", "kbps", "codec", "stab")}
        self.d.start(addr, params["model"], params["ssid"], params["password"], params["url"],
                     params["res"], params["fps"], params["kbps"], params["codec"], params["stab"])
        with self.lock:                 # erst nach angenommenem Start merken
            self.desired[addr] = params
            self.tries.pop(addr, None)
            self._save_active()

    def stop(self, address=None):
        with self.lock:
            if address:
                self.desired.pop(address.upper(), None)
            else:
                self.desired.clear()
            self._save_active()
        self.d.stop(address)

    def _net_ip(self):
        """IP der Schnittstelle, die in der Oberfläche als Kameranetz gewählt ist."""
        try:
            with open(os.path.join(self.state, "camera-net.json")) as f:
                name = json.load(f).get("iface")
        except (OSError, ValueError):
            return None
        return iface_ip(name) if isinstance(name, str) and name else None

    def follow_ip(self):
        """Ändert sich die Adresse der Box im Kameranetz (z. B. neue MAC am USB-Router), tragen wir sie in
        die gespeicherten Ziele ein und verbinden die betroffenen Kameras neu. Erst nach zwei gleichen
        Messungen, damit kurze Zwischenzustände nichts auslösen."""
        import re
        ip = self.ipfn()
        if not ip:
            self.ip_pending = None
            return []
        with self.lock:
            stale = [a for a, p in self.desired.items()
                     if re.match(r"^rtmp://\d+\.\d+\.\d+\.\d+[:/]", p.get("url", ""))
                     and re.match(r"^rtmp://([^:/]+)", p["url"]).group(1) != ip]
        if not stale:
            self.ip_pending = None
            return []
        n = (self.ip_pending[1] + 1) if self.ip_pending and self.ip_pending[0] == ip else 1
        self.ip_pending = (ip, n)
        if n < 2:
            return []
        self.ip_pending = None
        with self.lock:
            for a in stale:
                p = self.desired[a]
                p["url"] = re.sub(r"^(rtmp://)[^:/]+", lambda m: m.group(1) + ip, p["url"])
            self._save_active()
        print(f"dji-daemon: Adresse der Box im Kameranetz ist jetzt {ip}, {len(stale)} Kamera(s) werden neu verbunden", flush=True)
        for a in stale:
            self.up_since.pop(a, None)
            self.quiet.pop(a, None)
            self.tries.pop(a, None)
            self.d.stop(a)       # beendet die Sitzung; der Wächter startet sie mit der neuen Adresse neu
        return stale

    # -- Wächter: gewünschte Kameras am Laufen halten
    def supervise_once(self, now=None):
        now = now if now is not None else time.time()
        if self.follow_ip():
            time.sleep(3)
        sessions = self.d.status()["sessions"]
        with self.lock:
            wanted = dict(self.desired)
        net = self.net_info()
        net_down = bool(net["iface"]) and not net["ip"]
        if net_down and not self.net_down_logged:
            print(f"dji-daemon: Kameranetz {net['iface']} ist nicht verfügbar: Stream-Wächter pausiert", flush=True)
        if self.net_down_logged and not net_down:
            # Kameranetz ist zurück: Wartezeiten zurücksetzen und kurz warten, bis das WLAN des Routers steht
            self.tries.clear()
            self.hold_until = now + NET_SETTLE
            print(f"dji-daemon: Kameranetz {net['iface']} ist wieder da, Kameras werden in {NET_SETTLE} s neu verbunden", flush=True)
        self.net_down_logged = net_down
        started = False                      # höchstens eine neue Verbindung je Durchlauf (die Kameras nacheinander)
        # Ohne Kameranetz kann kein Stream ankommen: dann nicht ständig neu verbinden
        live = publishing_keys() if (self.watch_rtmp and not net_down) else None
        for addr, p in wanted.items():
            st = (sessions.get(addr) or {}).get("state", "idle")
            if st == "streaming" and live is not None:
                key = p["url"].rstrip("/").rsplit("/", 1)[-1]
                self.up_since.setdefault(addr, now)
                if key in live:
                    self.quiet[addr] = None
                elif now - self.up_since[addr] > GRACE:
                    since = self.quiet.get(addr) or now
                    self.quiet[addr] = since
                    if now - since > SILENT_LIMIT:
                        print(f"dji-daemon: {addr[-8:]} Stream kommt nicht an, Sitzung wird neu gestartet", flush=True)
                        self.quiet[addr] = None
                        self.up_since.pop(addr, None)
                        self.d.stop(addr)           # beendet die Sitzung; der Wächter startet sie unten neu
                        time.sleep(3)
                        sessions = self.d.status()["sessions"]
                        st = (sessions.get(addr) or {}).get("state", "idle")
            elif st != "streaming":
                self.up_since.pop(addr, None)
                self.quiet.pop(addr, None)
            if st not in ("idle", "failed"):
                if st == "streaming":
                    self.tries[addr] = (0, 0)     # läuft: Zähler zurücksetzen
                continue                           # sonst in Arbeit: Zähler behalten, damit die Wartezeit wächst
            n, nxt = self.tries.get(addr, (0, 0))
            if now < nxt or now < self.hold_until or started:
                continue
            if net_down:
                continue                           # ohne Kameranetz kann keine Kamera streamen: nicht verbrauchen
            started = True
            delay = BACKOFF[min(n, len(BACKOFF) - 1)]
            self.tries[addr] = (n + 1, now + delay)
            print(f"dji-daemon: {addr[-8:]} wird wieder verbunden (Versuch {n + 1})", flush=True)
            try:
                self.d.start(addr, p["model"], p["ssid"], p["password"], p["url"], p["res"],
                             p["fps"], p["kbps"], p["codec"], p["stab"])
            except ValueError as e:
                print(f"dji-daemon: {addr[-8:]} {e}", flush=True)

    def supervise_loop(self):
        time.sleep(5)    # BlueZ nach dem Start kurz Zeit geben
        while True:
            try:
                self.supervise_once()
            except Exception as e:     # nie den Wächter beenden
                print(f"dji-daemon: Wächterfehler {e!r}", flush=True)
            time.sleep(5)


def make_handler(dm):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _auth(self):
            import hmac
            return hmac.compare_digest(self.headers.get("X-Token", ""), dm.token)

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._auth():
                return self._send(401, {"error": "kein Zugriff"})
            if self.path == "/status":
                return self._send(200, dm.status())
            self._send(404, {"error": "unbekannt"})

        def do_POST(self):
            if not self._auth():
                return self._send(401, {"error": "kein Zugriff"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 8192)) or b"{}")
                if self.path == "/scan":
                    dm.d.scan(30)
                elif self.path == "/start":
                    dm.start(body)
                elif self.path == "/stop":
                    dm.stop(body.get("address"))
                else:
                    return self._send(404, {"error": "unbekannt"})
                self._send(200, {"ok": True})
            except (ValueError, KeyError, TypeError) as e:
                self._send(400, {"error": str(e) or "Ungültige Anfrage"})
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="/var/lib/pipbox")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    dm = Daemon(args.state)
    threading.Thread(target=dm.supervise_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(dm))
    print(f"dji-daemon: bereit auf 127.0.0.1:{args.port}, gewünschte Kameras: {len(dm.desired)}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
