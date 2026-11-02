"""Geschwindigkeits- und Verbindungstest je Leitung (auch während der Sendung).

Jede Messung bindet ihre Verbindungen an die Adresse der Leitung und nimmt damit genau den Weg, den auch die Sendewege (srtla) nehmen.
Gemessen wird gegen den öffentlichen Test von Cloudflare (speed.cloudflare.com, feste Adresse im Programm, keine Eingabe möglich). Die Datenmenge ist
begrenzt (Herunterladen höchstens 30 MB, Hochladen höchstens 15 MB, je höchstens 5 Sekunden), auf einer langsamen Leitung wird entsprechend weniger übertragen.
Schlägt ein Schritt fehl, steht der Grund im Klartext in der Antwort (keine Adresse, kein Router, falsche Karte, keine Antwort aus dem Internet, DNS, TLS, ...).
"""
import json
import os
import re
import socket
import ssl
import statistics
import subprocess
import threading
import time

from srv.netinfo import iface_ips

HOST = "speed.cloudflare.com"
STATUS_FILE = "/run/pipbox-uplinks/status.json"
STATUS_MAX_AGE = 45
TCP_TARGET = ("1.1.1.1", 443)
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 6.0
DOWN_SECONDS, UP_SECONDS = 5.0, 5.0
DOWN_MAX, UP_MAX = 30_000_000, 15_000_000
MIN_GAP = 8.0
IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
NO_REPLY_SECONDS = 25


class TestError(Exception):
    def __init__(self, code, text, hint=""):
        super().__init__(text)
        self.code, self.text, self.hint = code, text, hint


ERRORS = {
    "keine_adresse": ("Keine Adresse auf dieser Leitung.", "Kabel und Router prüfen. Der Router muss der Box per DHCP eine Adresse geben."),
    "kein_router": ("Kein Router gefunden (keine Standardroute auf dieser Karte).", "Router oder Modem neu starten, Kabel prüfen."),
    "keine_route": ("Keine Route ins Internet über diese Leitung.", "Router hat keinen Weg ins Internet, oder die Leitung ist nicht verbunden."),
    "falsche_karte": ("Pakete dieser Leitung verlassen die Box über eine andere Karte.",
                      "Das Quelladress-Routing der Box stimmt nicht. Der Leitungs-Wächter richtet es neu ein; bleibt es, bitte Protokolle senden."),
    "zeitueberschreitung": ("Keine Antwort aus dem Internet über diese Leitung.", "Der Router ist da, aber die Leitung hat kein Internet oder blockiert den Test."),
    "dns": ("Namensauflösung (DNS) funktioniert nicht.", "Der Router liefert keinen DNS-Server oder er antwortet nicht."),
    "tls": ("Gesicherte Verbindung (TLS) zum Testserver fehlgeschlagen.", "Stimmt die Uhrzeit der Box? Manche Netze filtern gesicherte Verbindungen."),
    "zertifikat": ("Das Zertifikat des Testservers ist ungültig.", "Meist ist die Uhrzeit der Box falsch oder ein Netz fängt den Datenverkehr ab."),
    "zurueckgesetzt": ("Verbindung zum Testserver wurde zurückgesetzt.", "Ein Router oder Anbieter blockiert den Test."),
    "server": ("Der Testserver hat nicht wie erwartet geantwortet.", "Später noch einmal versuchen."),
    "zu_langsam": ("Die Messung hat auf dieser Leitung keine Daten übertragen.", "Die Leitung ist praktisch tot oder blockiert."),
    "abgebrochen": ("Die Messung ist abgebrochen.", ""),
}


def fail(code):
    text, hint = ERRORS.get(code, (code, ""))
    raise TestError(code, text, hint)


def route_dev(ip):
    """Karte, über die Pakete mit dieser Absenderadresse die Box verlassen (ip route get)."""
    try:
        r = subprocess.run(["ip", "-4", "route", "get", TCP_TARGET[0], "from", ip], capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\bdev (\S+)", r.stdout or "")
    return m.group(1) if m else None


def connect(ip, addr, timeout=CONNECT_TIMEOUT):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.bind((ip, 0))
        s.connect(addr)
    except socket.timeout:
        s.close()
        fail("zeitueberschreitung")
    except ConnectionRefusedError:
        s.close()
        fail("server")
    except ConnectionResetError:
        s.close()
        fail("zurueckgesetzt")
    except OSError as e:
        s.close()
        fail("keine_route" if e.errno in (101, 113, 99) else "zeitueberschreitung")
    return s


def tls(sock):
    try:
        ctx = ssl.create_default_context()
        t = ctx.wrap_socket(sock, server_hostname=HOST)
        t.settimeout(READ_TIMEOUT)
        return t
    except ssl.SSLCertVerificationError:
        sock.close()
        fail("zertifikat")
    except ssl.SSLError:
        sock.close()
        fail("tls")
    except socket.timeout:
        sock.close()
        fail("zeitueberschreitung")
    except ConnectionResetError:
        sock.close()
        fail("zurueckgesetzt")
    except OSError:
        sock.close()
        fail("tls")


def read_head(t):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = t.recv(4096)
        if not chunk:
            fail("server")
        buf += chunk
        if len(buf) > 16384:
            fail("server")
    head, _, rest = buf.partition(b"\r\n\r\n")
    m = re.match(rb"HTTP/1\.[01] (\d{3})", head)
    if not m or m.group(1) != b"200":
        fail("server")
    return rest


def resolve():
    try:
        infos = socket.getaddrinfo(HOST, 443, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        fail("dns")
    if not infos:
        fail("dns")
    return infos[0][4][0]


def _worker_down(ip, addr, deadline, share, out, idx):
    got = 0
    try:
        t = tls(connect(ip, (addr, 443)))
        t.sendall(f"GET /__down?bytes={DOWN_MAX} HTTP/1.1\r\nHost: {HOST}\r\nUser-Agent: irl4you-box\r\nConnection: close\r\n\r\n".encode())
        rest = read_head(t)
        t0 = time.monotonic()
        got = len(rest)
        while time.monotonic() < deadline and got < share:
            chunk = t.recv(65536)
            if not chunk:
                break
            got += len(chunk)
        out[idx] = (got, max(time.monotonic() - t0, 0.001), None)
        t.close()
    except TestError as e:
        out[idx] = (got, 0.0, e)
    except (OSError, ssl.SSLError):
        out[idx] = (got, 0.0, TestError("zurueckgesetzt", ERRORS["zurueckgesetzt"][0], ERRORS["zurueckgesetzt"][1]))


def measure_down(ip, addr):
    """Herunterladen mit zwei Verbindungen gleichzeitig; -> Mbit/s"""
    out = [None, None]
    deadline = time.monotonic() + DOWN_SECONDS
    ths = [threading.Thread(target=_worker_down, args=(ip, addr, deadline, DOWN_MAX // 2, out, i), daemon=True) for i in range(2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(DOWN_SECONDS + CONNECT_TIMEOUT + 4)
    done = [o for o in out if o]
    errs = [o[2] for o in done if o[2]]
    total = sum(o[0] for o in done)
    if total < 20_000:
        raise errs[0] if errs else TestError("zu_langsam", *ERRORS["zu_langsam"])
    secs = max((o[1] for o in done if o[1] > 0), default=DOWN_SECONDS)
    return total * 8 / secs / 1e6


def _worker_up(ip, addr, deadline, share, out, idx, block):
    sent = 0
    t0 = None
    try:
        s = connect(ip, (addr, 443))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        t = tls(s)
        t.sendall(f"POST /__up HTTP/1.1\r\nHost: {HOST}\r\nUser-Agent: irl4you-box\r\nContent-Type: text/plain\r\nContent-Length: {share}\r\nConnection: close\r\n\r\n".encode())
        t0 = time.monotonic()
        while time.monotonic() < deadline and sent < share:
            n = min(len(block), share - sent)
            t.sendall(block[:n])
            sent += n
        elapsed = max(time.monotonic() - t0, 0.001)
        out[idx] = (sent, elapsed, None)
        try:
            t.close()
        except OSError:
            pass
    except TestError as e:
        out[idx] = (sent, 0.0, e)
    except (OSError, ssl.SSLError):
        elapsed = max(time.monotonic() - t0, 0.001) if t0 else 0.0
        out[idx] = (sent, elapsed, None if sent > 20_000 else TestError("zurueckgesetzt", ERRORS["zurueckgesetzt"][0], ERRORS["zurueckgesetzt"][1]))


def measure_up(ip, addr):
    """Hochladen mit zwei Verbindungen gleichzeitig; -> Mbit/s (kleiner Sendepuffer, damit die Zahl nicht vom Puffer der Box kommt)"""
    out = [None, None]
    block = os.urandom(65536)
    deadline = time.monotonic() + UP_SECONDS
    ths = [threading.Thread(target=_worker_up, args=(ip, addr, deadline, UP_MAX // 2, out, i, block), daemon=True) for i in range(2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(UP_SECONDS + CONNECT_TIMEOUT + 4)
    done = [o for o in out if o]
    errs = [o[2] for o in done if o[2]]
    total = sum(o[0] for o in done)
    if total < 20_000:
        raise errs[0] if errs else TestError("zu_langsam", *ERRORS["zu_langsam"])
    secs = max((o[1] for o in done if o[1] > 0), default=UP_SECONDS)
    return total * 8 / secs / 1e6


def measure_rtt(ip, n=3):
    vals = []
    last = None
    for _ in range(n):
        t0 = time.monotonic()
        try:
            s = connect(ip, TCP_TARGET, 3.0)
            vals.append((time.monotonic() - t0) * 1000)
            s.close()
        except TestError as e:
            last = e
    if not vals:
        raise last or TestError("zeitueberschreitung", *ERRORS["zeitueberschreitung"])
    return round(statistics.median(vals))


def read_health(path=None):
    """Ergebnis des Leitungs-Wächters (pipbox-uplinks), None wenn nicht da oder zu alt."""
    path = path or STATUS_FILE
    try:
        if time.time() - os.stat(path).st_mtime > STATUS_MAX_AGE:
            return None
        with open(path, "rb") as f:
            d = json.loads(f.read(65536).decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    ifs = d.get("ifaces") if isinstance(d, dict) else None
    if not isinstance(ifs, dict):
        return None
    out = {}
    for name, e in ifs.items():
        if isinstance(name, str) and IFACE_RE.match(name) and isinstance(e, dict):
            out[name] = {"ok": e.get("ok"), "err": e.get("err"), "text": e.get("text"), "rtt_ms": e.get("rtt_ms"), "gw": e.get("gw") is not None,
                         "since": e.get("since"), "managed": bool(e.get("managed")), "table": e.get("table")}
    return out


class UplinkTests:
    def __init__(self, demo=False, sending=lambda: False, ips=iface_ips, skip=lambda: set()):
        self.demo = demo
        self.skip = skip
        self.sending = sending
        self.ips = ips
        self.lock = threading.Lock()
        self.tests = {}
        self.queue = []
        self.worker = None
        self.noreply = {}

    # ---- Abfragen
    def status(self):
        with self.lock:
            tests = {k: dict(v) for k, v in self.tests.items()}
            busy = bool(self.queue) or any(v.get("state") in ("läuft", "wartet") for v in tests.values())
        return {"tests": tests, "busy": busy, "health": read_health(), "sending": bool(self.sending())}

    def diagnose(self, selected, links, sending):
        """Meldung je Leitung: ausgefallen laut Wächter, oder verbunden, aber ohne Antwort des SRT-Servers (nur während der Sendung)."""
        health = read_health() or {}
        now = time.time()
        out = {}
        sel = set(selected or [])
        skip = self.skip()
        for o in self.ips():
            name = o["iface"]
            if name in skip:
                continue
            h = health.get(name) or {}
            if h.get("ok") is False:
                text, hint = ERRORS.get(h.get("err") or "", (h.get("text") or "Kein Internet über diese Leitung.", ""))
                out[name] = {"level": "bad", "text": text, "hint": hint}
            elif h.get("err") in ("falsche_karte", "kein_router"):
                text, hint = ERRORS[h["err"]]
                out[name] = {"level": "bad", "text": text, "hint": hint}
            elif sending and name in sel and name in (links or {}) and (links[name] or {}).get("srtt") is None:
                since = self.noreply.setdefault(name, now)
                if now - since >= NO_REPLY_SECONDS:
                    out[name] = {"level": "warn", "text": "Diese Leitung hat Internet, aber der SRT-Server antwortet auf ihr nicht.",
                                 "hint": "Der Anbieter oder Router blockiert vermutlich UDP zum Server oder verändert die Pakete. Test starten für Einzelheiten."}
            if not (sending and name in sel and name in (links or {}) and (links[name] or {}).get("srtt") is None):
                self.noreply.pop(name, None)
        return out

    # ---- Starten
    def start(self, ifaces, allow_all=False):
        skip = self.skip()
        known = {o["iface"]: o["ip"] for o in self.ips() if o.get("ip") and o["iface"] not in skip}
        names = []
        for n in ifaces:
            if not isinstance(n, str) or not IFACE_RE.match(n):
                raise ValueError("Leitung ungültig")
            if n in skip:
                raise ValueError("Auf dieser Karte läuft der Hotspot der Box; ein Test ist dort nicht sinnvoll")
            if n not in names:
                names.append(n)
        if allow_all:
            names = [n for n in known]
        if not names:
            raise ValueError("Keine Leitung zum Testen gefunden")
        now = time.time()
        with self.lock:
            for n in names:
                cur = self.tests.get(n) or {}
                if cur.get("state") in ("läuft", "wartet"):
                    continue
                if now - cur.get("finished", 0) < MIN_GAP:
                    continue
                self.tests[n] = {"state": "wartet", "time": int(now), "finished": 0}
                self.queue.append(n)
            if self.queue and (self.worker is None or not self.worker.is_alive()):
                self.worker = threading.Thread(target=self._run, daemon=True)
                self.worker.start()
        return self.status()

    def _run(self):
        while True:
            with self.lock:
                if not self.queue:
                    return
                n = self.queue.pop(0)
                self.tests[n] = {"state": "läuft", "time": int(time.time()), "finished": 0, "step": "Start", "during_send": bool(self.sending())}
            res = self._one(n)
            with self.lock:
                res["finished"] = time.time()
                res.setdefault("during_send", self.tests.get(n, {}).get("during_send", False))
                self.tests[n] = res

    def _step(self, name, text):
        with self.lock:
            if name in self.tests:
                self.tests[name]["step"] = text

    def _one(self, name):
        t0 = time.time()
        res = {"state": "fertig", "time": int(t0), "down_mbit": None, "up_mbit": None, "rtt_ms": None, "error": None, "text": None, "hint": None}
        try:
            if self.demo:
                time.sleep(1.5)
                res.update(down_mbit=42.5, up_mbit=8.3, rtt_ms=31)
                return res
            ip = {o["iface"]: o["ip"] for o in self.ips() if o.get("ip")}.get(name)
            if not ip:
                fail("keine_adresse")
            self._step(name, "Weg prüfen")
            dev = route_dev(ip)
            if dev and dev != name:
                fail("falsche_karte")
            self._step(name, "Verbindung")
            res["rtt_ms"] = measure_rtt(ip)
            self._step(name, "Namensauflösung")
            addr = resolve()
            self._step(name, "Herunterladen")
            res["down_mbit"] = round(measure_down(ip, addr), 2)
            self._step(name, "Hochladen")
            res["up_mbit"] = round(measure_up(ip, addr), 2)
        except TestError as e:
            res.update(state="fehler", error=e.code, text=e.text, hint=e.hint)
        except Exception as e:                      # nie die Oberfläche mitreißen
            res.update(state="fehler", error="abgebrochen", text=ERRORS["abgebrochen"][0] + f" ({type(e).__name__})", hint="")
        res["seconds"] = round(time.time() - t0, 1)
        return res
