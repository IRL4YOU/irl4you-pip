#!/usr/bin/env python3
"""Vorschau des Sendebilds (Issue #52).

Zeigt das Bild, das wirklich gesendet wird: das fertig gemischte und kodierte Bild, nicht einzelne Kameras. Dazu liest ein kurzlebiger Prozess die
SRT-Datenpakete mit, die belacoder lokal an srtla_send schickt (127.0.0.1:9100, unverschlüsselt), ordnet sie, entpackt den MPEG-TS-Strom, dekodiert ihn
mit dem Hardware-Dekoder und schickt kleine JPEG-Bilder als Motion-JPEG (multipart/x-mixed-replace) an den Browser.

Die Sendekette wird dabei nicht angefasst (kein Abzweig in der Pipeline, kein Eingriff in belacoder): Ein Seitenzweig vor dem Encoder ließ den
Mischer abbrechen, hier wird nur mitgelesen. Ohne Zuschauer läuft nichts. Eine Vorschau endet, wenn der Browser die Verbindung schließt, nach
MAX_SECONDS oder wenn der Sender stoppt.

Gemessen auf der Box (Orange Pi 5 Plus, 1080p30 HEVC, 640x360, 30 Bilder/s): rund 18 % eines Kerns, 9 MB Speicher, etwa 3 Mbit/s, die Sendung bleibt unberührt.

Rechte: Der Webserver läuft als Benutzer pipbox und darf weder rohe Pakete lesen noch den Hardware-Dekoder benutzen (/dev/mpp_service gehört root). Die Arbeit macht darum ein
eigener kleiner Dienst (pipbox-preview.service, Aufruf: pipbox_preview.py --serve) nur mit dem Recht CAP_NET_RAW. Er wird von systemd erst gestartet, wenn jemand die Vorschau
öffnet (pipbox-preview.socket), und beendet sich nach IDLE_EXIT Sekunden ohne Zuschauer. Der Webserver spricht über einen Unix-Socket (Gruppe pipbox, Modus 0660) mit ihm:
eine JSON-Zeile hin, eine JSON-Zeile zurück, danach (nur bei "stream") die Motion-JPEG-Daten, bis eine Seite schließt."""
import ctypes
import json
import os
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

LISTEN_PORT = 9100                # belacoder -> srtla_send (wie in pipbox_send.py)
SOCKET = "/run/pipbox-preview.sock"
SEND_STATUS = "/run/pipbox-send/status.json"
IDLE_EXIT = 60                    # der Dienst beendet sich nach so vielen Sekunden ohne Zuschauer (systemd startet ihn bei der nächsten Anfrage neu)
MAX_REQUEST = 512                 # längste Anfragezeile in Byte
MAX_SECONDS = 600                 # eine Vorschau endet nach 10 Minuten, die Seite fragt dann "Weiter ansehen?"
LONG_SECONDS = 24 * 3600            # mit dem Häkchen "dauerhaft" in der Oberfläche (je Browser): so lange, danach startet die Seite von selbst neu
MAX_VIEWERS = 2                   # gleichzeitige Vorschauen (jede kostet rund 18 % eines Kerns)
CHECK_EVERY = 3.0                 # so oft wird geprüft, ob der Sender noch läuft
FIRST_FRAME_WAIT = 12.0           # bis zum ersten Bild: Schlüsselbild abwarten (alle 2 s), dann dekodieren
REORDER_WINDOW = 48               # so viele Pakete warten auf ein verspätetes (SRT wiederholt verlorene Pakete); danach wird die Lücke übersprungen
BOUNDARY = "pbframe"
FPS_RANGE = (1, 30)
WIDTH_RANGE = (160, 1280)
SO_ATTACH_FILTER = 26
SKF_AD_PKTTYPE = 0xfffff004       # BPF: Paketart (auf lo gibt es jedes Paket als ausgehende und als eingehende Kopie)


class Unavailable(Exception):
    """Die Vorschau lässt sich gerade nicht starten (Text für die Oberfläche)."""


class Busy(Unavailable):
    pass


def clamp(v, lo, hi, default):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def bpf_program(port):
    """Klassischer BPF-Filter: nur UDP-Pakete an den Zielport, keine Fragmente, nicht die ausgehende Kopie auf lo.
    Der Kernel wirft alles andere weg (RTMP-Ströme der Kameras laufen auch über lo und würden sonst Rechenzeit kosten)."""
    ins = [
        (0x30, 0, 0, 9),                  # ldb [9]       Protokoll
        (0x15, 0, 8, 17),                 # jeq 17        UDP, sonst verwerfen
        (0x28, 0, 0, 6),                  # ldh [6]       Fragment-Feld
        (0x45, 6, 0, 0x1fff),             # jset 0x1fff   Fragment: verwerfen
        (0xb1, 0, 0, 0),                  # ldxb 4*([0]&0xf)   Länge des IP-Kopfs
        (0x48, 0, 0, 2),                  # ldh [x+2]     Zielport
        (0x15, 0, 3, port),               # jeq port      sonst verwerfen
        (0x20, 0, 0, SKF_AD_PKTTYPE),     # ld pkt_type
        (0x15, 1, 0, 4),                  # jeq OUTGOING  ausgehende Kopie verwerfen
        (0x06, 0, 0, 0x40000),            # annehmen
        (0x06, 0, 0, 0),                  # verwerfen
    ]
    return b"".join(struct.pack("HBBI", c, jt, jf, k) for c, jt, jf, k in ins), len(ins)


def attach_filter(sock, port):
    prog, n = bpf_program(port)
    buf = ctypes.create_string_buffer(prog)
    sock.setsockopt(socket.SOL_SOCKET, SO_ATTACH_FILTER, struct.pack("HL", n, ctypes.addressof(buf)))
    return buf


def srt_data(pkt):
    """(Folgenummer, TS-Nutzdaten) eines SRT-Datenpakets aus einem IPv4/UDP-Paket, sonst None (Steuerpaket, verschlüsselt, zu kurz)."""
    if len(pkt) < 36 or pkt[0] >> 4 != 4 or pkt[9] != 17:
        return None
    pl = pkt[(pkt[0] & 15) * 4 + 8:]
    if len(pl) < 16 + 188:
        return None
    w0, w1 = struct.unpack("!II", pl[:8])
    if w0 & 0x80000000:                            # Steuerpaket (Handshake, ACK, NAK, Keepalive)
        return None
    if (w1 >> 27) & 3:                             # verschlüsselt: nicht lesbar
        return None
    return w0, pl[16:]


class Reorder:
    """Bringt SRT-Datenpakete in die richtige Reihenfolge. SRT sendet verlorene Pakete später noch einmal; solche Wiederholungen füllen Lücken.
    Ein Paket, das länger als REORDER_WINDOW Pakete fehlt, wird aufgegeben (der Dekoder fängt sich am nächsten Schlüsselbild)."""

    def __init__(self, window=REORDER_WINDOW):
        self.window = window
        self.buf = {}
        self.nxt = None
        self.skipped = 0

    def push(self, seq, payload):
        out = []
        if self.nxt is None:
            self.nxt = seq
        if ((seq - self.nxt) & 0x7fffffff) > 0x3fffffff:      # zu alt (schon ausgegeben oder aufgegeben, oder die zweite Kopie)
            return out
        self.buf[seq] = payload
        while self.nxt in self.buf:
            out.append(self.buf.pop(self.nxt))
            self.nxt = (self.nxt + 1) & 0x7fffffff
        if len(self.buf) > self.window:
            self.nxt = min(self.buf, key=lambda x: (x - self.nxt) & 0x7fffffff)
            self.skipped += 1
            while self.nxt in self.buf:
                out.append(self.buf.pop(self.nxt))
                self.nxt = (self.nxt + 1) & 0x7fffffff
        return out


def detect_codec(pipeline_text):
    """h265 oder h264, je nachdem, welchen Encoder die Sendekette benutzt (steht in der Pipeline-Datei)."""
    t = (pipeline_text or "").lower()
    return "h265" if ("265" in t or "hevc" in t) else "h264"


def big_cpus(cpu_dir="/sys/devices/system/cpu"):
    """Nummern der schnellen Kerne (big.LITTLE über cpu_capacity), [] wenn alle gleich schnell sind (wie pipbox_live.big_cpus, ohne dessen Abhängigkeiten)."""
    cap = {}
    try:
        for d in os.listdir(cpu_dir):
            if d.startswith("cpu") and d[3:].isdigit():
                try:
                    with open("%s/%s/cpu_capacity" % (cpu_dir, d)) as f:
                        cap[int(d[3:])] = int(f.read().strip())
                except (OSError, ValueError):
                    pass
    except OSError:
        return []
    if not cap or max(cap.values()) == min(cap.values()):
        return []
    top = max(cap.values())
    return sorted(c for c, v in cap.items() if v >= top * 0.9)


def sending_now(path=SEND_STATUS, now=time.time):
    """Läuft der Sender? Der Sende-Dienst schreibt seinen Zustand alle 2 s nach /run/pipbox-send/status.json (jünger als 15 s, nicht im Beenden)."""
    try:
        with open(path) as f:
            st = json.load(f)
        return now() - float(st.get("time", 0)) < 15 and st.get("state") not in ("stopping", "refused")
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def little_cpus(big):
    """Alle Kerne außer den schnellen: dort läuft die Vorschau, damit die schnellen für die Sendung frei bleiben."""
    try:
        n = os.cpu_count() or 0
    except Exception:
        n = 0
    return [c for c in range(n) if c not in set(big)] if big and n else []


def gst_argv(codec, fps, width, cpus=None):
    """Kommandozeile des Dekoders: TS von stdin, Motion-JPEG nach stdout. Der Tonzweig läuft ohne Warten, sonst blockiert er tsdemux, solange das Video
    noch auf das erste Schlüsselbild wartet. Kleiner machen mit der einfachsten Methode (nearest-neighbour): das ist rund fünfmal billiger als bilinear."""
    width = width - width % 2
    height = (width * 9 // 16) - ((width * 9 // 16) % 2)
    pre = ["taskset", "-c", ",".join(map(str, cpus))] if cpus and shutil.which("taskset") else []
    return pre + ["gst-launch-1.0", "-q", "fdsrc", "fd=0", "!", "tsdemux", "name=t",
                  "t.", "!", f"video/x-{codec}", "!", "queue", "!", f"{codec}parse", "!", "mppvideodec",
                  "!", "videorate", "drop-only=true", "!", f"video/x-raw,framerate={fps}/1",
                  "!", "videoscale", "method=nearest-neighbour", "!", f"video/x-raw,width={width},height={height}",
                  "!", "mppjpegenc", "q-factor=60", "!", "multipartmux", f"boundary={BOUNDARY}", "!", "fdsink", "fd=1",
                  "t.", "!", "audio/x-opus", "!", "queue", "leaky=downstream", "!", "fakesink", "sync=false", "async=false"]


class Preview:
    def __init__(self, port=LISTEN_PORT, pipeline_path=None, big_cpus=None):
        self.port = port
        self.pipeline_path = pipeline_path
        self.big_cpus = big_cpus or globals()["big_cpus"]
        self.lock = threading.Lock()
        self.viewers = 0

    def codec(self):
        try:
            with open(self.pipeline_path) as f:
                return detect_codec(f.read())
        except (OSError, TypeError):
            return "h264"

    @staticmethod
    def limit(long=False):
        """Höchstdauer einer Vorschau: 10 Minuten, mit "long" (Häkchen in der Oberfläche) 24 Stunden."""
        return LONG_SECONDS if long else MAX_SECONDS

    def status(self, sending):
        """Für die Oberfläche: kann eine Vorschau starten, und wenn nicht, warum nicht."""
        why = None
        if not shutil.which("gst-launch-1.0"):
            why = "gst"
        elif os.geteuid() != 0:
            why = "root"
        elif not sending:
            why = "off"
        with self.lock:
            busy = self.viewers >= MAX_VIEWERS
        if why is None and busy:
            why = "busy"
        return {"available": why is None, "why": why, "codec": self.codec(), "max_seconds": MAX_SECONDS, "viewers": self.viewers}

    def _acquire(self):
        with self.lock:
            if self.viewers >= MAX_VIEWERS:
                raise Busy("busy")
            self.viewers += 1

    def _release(self):
        with self.lock:
            self.viewers = max(0, self.viewers - 1)

    def open_capture(self):
        """Raw-Socket auf lo, nur UDP an den Zielport (Filter im Kernel)."""
        try:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0003))
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
            attach_filter(sock, self.port)
            sock.bind(("lo", 0))
            sock.settimeout(0.5)
            return sock
        except (OSError, AttributeError) as e:
            raise Unavailable("capture: %s" % e)

    def spawn(self, fps, width):
        try:
            return subprocess.Popen(gst_argv(self.codec(), fps, width, little_cpus(self.big_cpus())), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, env=dict(os.environ, GST_MPP_NO_RGA="1"), bufsize=0)
        except OSError as e:
            raise Unavailable("gst: %s" % e)

    def stream(self, write, fps, width, still_sending, max_seconds=MAX_SECONDS, ready=None, gone=None):
        """Läuft, bis der Browser geht (write löst OSError aus), die Zeit um ist oder der Sender stoppt. write(bytes) gibt Daten an den Browser,
        still_sending() sagt, ob der Sender noch läuft, ready() wird einmal aufgerufen, sobald alles gestartet ist, gone() sagt, ob die Gegenseite schon weg ist (auch ohne dass gerade Daten fließen). Rückgabe: Grund des Endes ("client", "zeit", "sender", "kein-bild", "fehler")."""
        fps = clamp(fps, *FPS_RANGE, 30)
        width = clamp(width, *WIDTH_RANGE, 640)
        self._acquire()
        sock = proc = None
        stop = threading.Event()
        try:
            sock = self.open_capture()
            proc = self.spawn(fps, width)
            if ready is not None:
                ready()                                    # Mitlesen und Dekoder laufen: ab hier kommen Daten (oder "kein-bild")

            def feed():
                ro = Reorder()
                try:
                    while not stop.is_set():
                        try:
                            pkt = sock.recv(65535)
                        except socket.timeout:
                            continue
                        got = srt_data(pkt)
                        if got is None:
                            continue
                        for chunk in ro.push(*got):
                            proc.stdin.write(chunk)
                except (OSError, ValueError):
                    pass
                finally:
                    stop.set()
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass

            t = threading.Thread(target=feed, name="preview-feed", daemon=True)
            t.start()
            t0 = time.monotonic()
            first = False
            last_check = t0
            reason = "client"
            fd = proc.stdout.fileno()
            while True:
                now = time.monotonic()
                if now - t0 > max_seconds:
                    reason = "zeit"
                    break
                if gone is not None and gone():
                    reason = "client"
                    break
                if stop.is_set() and not select.select([fd], [], [], 0)[0]:
                    reason = "fehler"
                    break
                if not first and now - t0 > FIRST_FRAME_WAIT:
                    reason = "kein-bild"
                    break
                if now - last_check > CHECK_EVERY:
                    last_check = now
                    if not still_sending():
                        reason = "sender"
                        break
                if not select.select([fd], [], [], 1.0)[0]:
                    continue
                data = os.read(fd, 65536)
                if not data:
                    reason = "fehler" if not first else "client"
                    break
                first = True
                try:
                    write(data)
                except OSError:
                    reason = "client"
                    break
            return reason
        finally:
            stop.set()
            if proc is not None:
                for fn in (proc.terminate,):
                    try:
                        fn()
                    except OSError:
                        pass
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.wait(timeout=3)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                for f in (proc.stdout, proc.stdin):
                    try:
                        f.close()
                    except (OSError, AttributeError):
                        pass
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            self._release()


def iface_addrs(names):
    """IPv4-Adresse je Netzwerkschnittstelle ({Name: Adresse}); was keine hat, fehlt."""
    out = {}
    try:
        import fcntl
    except ImportError:
        return out
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for n in names:
            try:
                out[n] = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", n[:15].encode()))[20:24])      # SIOCGIFADDR
            except OSError:
                pass
    finally:
        s.close()
    return out


def default_route_iface(path="/proc/net/route"):
    """Schnittstelle der Standardroute (kleinste Metrik), sonst None."""
    best = None
    try:
        with open(path) as f:
            for line in f.read().splitlines()[1:]:
                c = line.split()
                if len(c) >= 7 and c[1] == "00000000" and int(c[3], 16) & 2:
                    m = int(c[6])
                    if best is None or m < best[0]:
                        best = (m, c[0])
    except (OSError, ValueError):
        return None
    return best[1] if best else None


def is_home_address(ip):
    """Kommt der Browser aus dem Heimnetz (private, Link-lokale oder lokale Adresse)? True/False, None wenn die Adresse unlesbar ist. Adressen des Tailnets (100.64.0.0/10) und
    öffentliche Adressen zählen als "außerhalb"."""
    import ipaddress
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return None
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    if a in ipaddress.ip_network("100.64.0.0/10"):
        return False
    return bool(a.is_private or a.is_loopback or a.is_link_local)


class Traffic:
    """Verkehr der Vorschau zum Browser. Er steht sonst im Upload-Zähler der Netzwerkkarte (/proc/net/dev) und verfälscht die Anzeige der Sendung
    (3 Mbit/s zusätzlich bei voller Bildrate). Der Webserver meldet, was er je Verbindung (lokale Adresse) an den Browser schickt; adjust() zieht das
    samt Aufschlag für Netzwerkköpfe von der Schnittstelle ab, die diese Adresse hat. Kommt der Browser über einen Proxy auf der Box (Tailscale, Adresse
    127.0.0.1) oder ist die Adresse unbekannt, gilt die Schnittstelle der Standardroute."""
    TX_FACTOR = 1.046          # Ethernet 14 + IP 20 + TCP 32 Byte je 1448 Byte Nutzdaten
    RX_FACTOR = 0.01           # Bestätigungen des Browsers (gemessen 0,9 %)

    def __init__(self, addrs=None, default=None):
        self.lock = threading.Lock()
        self.by_ip = {}
        self.addrs, self.default = addrs or iface_addrs, default or default_route_iface

    def add(self, ip, nbytes):
        with self.lock:
            self.by_ip[ip or ""] = self.by_ip.get(ip or "", 0) + nbytes

    def adjust(self, net):
        """net: {Schnittstelle: (rx, tx)} aus /proc/net/dev; zurück dasselbe ohne den Verkehr der Vorschau."""
        with self.lock:
            snap = dict(self.by_ip)
        if not snap:
            return net
        addr = self.addrs(list(net))
        per = {}
        for ip, n in snap.items():
            ifc = next((i for i, a in addr.items() if a == ip), None) or self.default()
            if ifc in net:
                per[ifc] = per.get(ifc, 0) + n
        out = dict(net)
        for ifc, n in per.items():
            rx, tx = net[ifc]
            out[ifc] = (max(0, rx - int(n * self.RX_FACTOR)), max(0, tx - int(n * self.TX_FACTOR)))
        return out


TRAFFIC = Traffic()


def read_line(sock, limit=MAX_REQUEST):
    """Eine Zeile (bis \\n) byteweise lesen, damit danach die Nutzdaten unberührt im Socket bleiben. Zu lang oder zu früh geschlossen: ValueError."""
    out = b""
    while True:
        c = sock.recv(1)
        if not c:
            raise ValueError("Verbindung zu früh geschlossen")
        if c == b"\n":
            return out
        out += c
        if len(out) > limit:
            raise ValueError("Zeile zu lang")


def peer_gone(conn):
    """Hat die Gegenseite die Verbindung geschlossen? Ohne zu warten (select), denn recv() mit MSG_DONTWAIT wartet bei einem Socket mit Zeitgrenze trotzdem bis zu ihr."""
    try:
        if not select.select([conn], [], [], 0)[0]:
            return False
        return conn.recv(1, socket.MSG_PEEK) == b""
    except (ValueError, OSError):
        return True


class Service:
    """Der Dienst hinter dem Unix-Socket: nimmt Anfragen vom Webserver an. Anfragen: {"cmd": "status"} und {"cmd": "stream", "fps", "w", "long"}.
    Antwort immer zuerst eine JSON-Zeile: Status, {"ok": true} oder {"error": Grund}; bei "stream" folgen danach die Bilddaten."""

    def __init__(self, preview, sending=sending_now, idle=IDLE_EXIT):
        self.pv, self.sending, self.idle = preview, sending, idle
        self.lock = threading.Lock()
        self.active = 0
        self.last = time.monotonic()

    def serve(self, lsock):
        """Annehmen, bis IDLE_EXIT Sekunden lang niemand mehr da ist; dann Rückkehr (der Dienst endet, systemd startet ihn bei der nächsten Anfrage)."""
        lsock.settimeout(1.0)
        while True:
            try:
                conn, _ = lsock.accept()
            except socket.timeout:
                with self.lock:
                    busy = self.active
                if not busy and time.monotonic() - self.last >= self.idle:
                    return
                continue
            except OSError:
                return
            with self.lock:
                self.active += 1
            self.last = time.monotonic()
            threading.Thread(target=self._run, args=(conn,), name="preview-conn", daemon=True).start()

    def _run(self, conn):
        try:
            self.handle(conn)
        except (OSError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
            with self.lock:
                self.active -= 1
            self.last = time.monotonic()

    def _send(self, conn, obj):
        conn.sendall(json.dumps(obj).encode() + b"\n")

    def handle(self, conn):
        conn.settimeout(5)
        try:
            req = json.loads(read_line(conn))
            if not isinstance(req, dict):
                raise ValueError("keine Anfrage")
        except ValueError:
            return self._send(conn, {"error": "anfrage"})
        cmd = req.get("cmd")
        if cmd == "status":
            return self._send(conn, self.pv.status(self.sending()))
        if cmd != "stream":
            return self._send(conn, {"error": "anfrage"})
        st = self.pv.status(self.sending())
        if not st["available"]:
            return self._send(conn, {"error": st["why"]})
        try:
            self.pv.stream(conn.sendall, req.get("fps"), req.get("w"), self.sending, max_seconds=self.pv.limit(req.get("long") is True), gone=lambda: peer_gone(conn),
                           ready=lambda: (self._send(conn, {"ok": True}), conn.settimeout(15)))        # Schreibzeit je Stück: ein Browser, der nicht liest, hält den Platz höchstens 15 s
        except Busy:
            self._send(conn, {"error": "busy"})
        except Unavailable:
            self._send(conn, {"error": "capture"})


class Client:
    """Der Webserver: fragt den Dienst über den Unix-Socket. Ist er nicht erreichbar (nicht installiert, Socket fehlt), gilt "nicht verfügbar"."""

    def __init__(self, path=SOCKET, timeout=5.0):
        self.path, self.timeout = path, timeout

    def _call(self, req):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.path)
            s.sendall(json.dumps(req).encode() + b"\n")
        except OSError:
            s.close()
            raise
        return s

    def status(self):
        try:
            s = self._call({"cmd": "status"})
            try:
                st = json.loads(read_line(s, 4096))
            finally:
                s.close()
            return st if isinstance(st, dict) and "available" in st else {"available": False, "why": "missing"}
        except (OSError, ValueError):
            return {"available": False, "why": "missing"}

    def open(self, fps, width, long=False):
        """(Socket, Kopfzeile) einer Vorschau; bei einem Fehler des Dienstes (Socket, Kopfzeile mit "error") ist der Socket None. OSError, wenn der Dienst fehlt."""
        s = self._call({"cmd": "stream", "fps": clamp(fps, *FPS_RANGE, 30), "w": clamp(width, *WIDTH_RANGE, 640), "long": bool(long)})
        try:
            head = json.loads(read_line(s, 4096))
        except ValueError:
            s.close()
            return None, {"error": "capture"}
        if not isinstance(head, dict) or head.get("error") or not head.get("ok"):
            s.close()
            return None, {"error": (head or {}).get("error", "capture") if isinstance(head, dict) else "capture"}
        return s, head


def listen_socket(path=SOCKET):
    """Der Socket von systemd (Socket-Aktivierung, Dateinummer 3), sonst (Handstart zum Testen) selbst angelegt mit Gruppe pipbox und Modus 0660."""
    if os.environ.get("LISTEN_PID") == str(os.getpid()) and os.environ.get("LISTEN_FDS") == "1":
        return socket.socket(fileno=3)
    try:
        os.unlink(path)
    except OSError:
        pass
    ls = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    ls.bind(path)
    try:
        import grp
        os.chown(path, 0, grp.getgrnam("pipbox").gr_gid)
    except (ImportError, KeyError, OSError):
        pass
    os.chmod(path, 0o660)
    ls.listen(8)
    return ls


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["--serve"]:
        print("Aufruf: pipbox_preview.py --serve (Dienst der Vorschau, Anfragen über %s)" % SOCKET)
        return 2
    pv = Preview(pipeline_path="/var/tmp/pipbox/pipeline")
    Service(pv).serve(listen_socket())
    return 0


if __name__ == "__main__":
    sys.exit(main())
