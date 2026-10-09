#!/usr/bin/env python3
"""Vorschau des Sendebilds (Issue #52).

Zeigt das Bild, das wirklich gesendet wird: das fertig gemischte und kodierte Bild, nicht einzelne Kameras. Dazu liest ein kurzlebiger Prozess die
SRT-Datenpakete mit, die belacoder lokal an srtla_send schickt (127.0.0.1:9100, unverschlüsselt), ordnet sie, entpackt den MPEG-TS-Strom, dekodiert ihn
mit dem Hardware-Dekoder und schickt kleine JPEG-Bilder als Motion-JPEG (multipart/x-mixed-replace) an den Browser.

Die Sendekette wird dabei nicht angefasst (kein Abzweig in der Pipeline, kein Eingriff in belacoder): Ein Seitenzweig vor dem Encoder ließ den
Mischer abbrechen, hier wird nur mitgelesen. Ohne Zuschauer läuft nichts. Eine Vorschau endet, wenn der Browser die Verbindung schließt, nach
MAX_SECONDS oder wenn der Sender stoppt.

Gemessen auf der Box (Orange Pi 5 Plus, 1080p30 HEVC, 640x360, 30 Bilder/s): rund 18 % eines Kerns, 9 MB Speicher, etwa 3 Mbit/s, die Sendung bleibt unberührt."""
import ctypes
import os
import select
import shutil
import socket
import struct
import subprocess
import threading
import time

LISTEN_PORT = 9100                # belacoder -> srtla_send (wie in pipbox_send.py)
MAX_SECONDS = 600                 # eine Vorschau endet nach 10 Minuten, die Seite fragt dann "Weiter ansehen?"
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
        self.big_cpus = big_cpus or (lambda: [])
        self.lock = threading.Lock()
        self.viewers = 0

    def codec(self):
        try:
            with open(self.pipeline_path) as f:
                return detect_codec(f.read())
        except (OSError, TypeError):
            return "h264"

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

    def stream(self, write, fps, width, still_sending, max_seconds=MAX_SECONDS):
        """Läuft, bis der Browser geht (write löst OSError aus), die Zeit um ist oder der Sender stoppt. write(bytes) gibt Daten an den Browser,
        still_sending() sagt, ob der Sender noch läuft. Rückgabe: Grund des Endes ("client", "zeit", "sender", "kein-bild", "fehler")."""
        fps = clamp(fps, *FPS_RANGE, 30)
        width = clamp(width, *WIDTH_RANGE, 640)
        self._acquire()
        sock = proc = None
        stop = threading.Event()
        try:
            sock = self.open_capture()
            proc = self.spawn(fps, width)

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
