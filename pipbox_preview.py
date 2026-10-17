#!/usr/bin/env python3
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

LISTEN_PORT = 9100
SOCKET = "/run/pipbox-preview.sock"
SEND_STATUS = "/run/pipbox-send/status.json"
IDLE_EXIT = 60
MAX_REQUEST = 512
MAX_SECONDS = 600
LONG_SECONDS = 24 * 3600
MAX_VIEWERS = 2
CHECK_EVERY = 3.0
FIRST_FRAME_WAIT = 12.0
REORDER_WINDOW = 48
BOUNDARY = "pbframe"
FORMATS = ("mp4", "mjpeg")
H264_BITRATE = 1_500_000
FRAGMENT_FRAMES = 6
FPS_RANGE = (1, 30)
WIDTH_RANGE = (160, 1280)
SO_ATTACH_FILTER = 26
SKF_AD_PKTTYPE = 0xfffff004


class Unavailable(Exception):
    pass


class Busy(Unavailable):
    pass


def clamp(v, lo, hi, default):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def bpf_program(port):
    ins = [
        (0x30, 0, 0, 9),
        (0x15, 0, 8, 17),
        (0x28, 0, 0, 6),
        (0x45, 6, 0, 0x1fff),
        (0xb1, 0, 0, 0),
        (0x48, 0, 0, 2),
        (0x15, 0, 3, port),
        (0x20, 0, 0, SKF_AD_PKTTYPE),
        (0x15, 1, 0, 4),
        (0x06, 0, 0, 0x40000),
        (0x06, 0, 0, 0),
    ]
    return b"".join(struct.pack("HBBI", c, jt, jf, k) for c, jt, jf, k in ins), len(ins)


def attach_filter(sock, port):
    prog, n = bpf_program(port)
    buf = ctypes.create_string_buffer(prog)
    sock.setsockopt(socket.SOL_SOCKET, SO_ATTACH_FILTER, struct.pack("HL", n, ctypes.addressof(buf)))
    return buf


def srt_data(pkt):
    if len(pkt) < 36 or pkt[0] >> 4 != 4 or pkt[9] != 17:
        return None
    pl = pkt[(pkt[0] & 15) * 4 + 8:]
    if len(pl) < 16 + 188:
        return None
    w0, w1 = struct.unpack("!II", pl[:8])
    if w0 & 0x80000000:
        return None
    if (w1 >> 27) & 3:
        return None
    return w0, pl[16:]


class Reorder:

    def __init__(self, window=REORDER_WINDOW):
        self.window = window
        self.buf = {}
        self.nxt = None
        self.skipped = 0

    def push(self, seq, payload):
        out = []
        if self.nxt is None:
            self.nxt = seq
        if ((seq - self.nxt) & 0x7fffffff) > 0x3fffffff:
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
    t = (pipeline_text or "").lower()
    return "h265" if ("265" in t or "hevc" in t) else "h264"


def big_cpus(cpu_dir="/sys/devices/system/cpu"):
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
    try:
        with open(path) as f:
            st = json.load(f)
        return now() - float(st.get("time", 0)) < 15 and st.get("state") not in ("stopping", "refused")
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def little_cpus(big):
    try:
        n = os.cpu_count() or 0
    except Exception:
        n = 0
    return [c for c in range(n) if c not in set(big)] if big and n else []


def gst_argv(codec, fps, width, cpus=None, fmt="mjpeg"):
    width = width - width % 2
    height = (width * 9 // 16) - ((width * 9 // 16) % 2)
    pre = ["taskset", "-c", ",".join(map(str, cpus))] if cpus and shutil.which("taskset") else []
    if fmt == "mp4":
        out = ["!", "mpph264enc", f"bitrate={H264_BITRATE}", f"gop={fps}", "profile=main", "level=31", "!", "h264parse", "config-interval=-1",
               "!", "video/x-h264,stream-format=byte-stream,alignment=au", "!", "fdsink", "fd=1"]
    else:
        out = ["!", "mppjpegenc", "q-factor=60", "!", "multipartmux", f"boundary={BOUNDARY}", "!", "fdsink", "fd=1"]
    return pre + ["gst-launch-1.0", "-q", "fdsrc", "fd=0", "!", "tsdemux", "name=t",
                  "t.", "!", f"video/x-{codec}", "!", "queue", "!", f"{codec}parse", "!", "mppvideodec",
                  "!", "videorate", "drop-only=true", "!", f"video/x-raw,framerate={fps}/1",
                  "!", "videoscale", "method=nearest-neighbour", "!", f"video/x-raw,width={width},height={height}"] + out + \
                 ["t.", "!", "audio/x-opus", "!", "queue", "leaky=downstream", "!", "fakesink", "sync=false", "async=false"]


def _box(typ, *parts):
    body = b"".join(parts)
    return struct.pack(">I4s", 8 + len(body), typ) + body


def _full(typ, version, flags, *parts):
    return _box(typ, struct.pack(">I", (version << 24) | flags), *parts)


_MATRIX = struct.pack(">9I", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)


def init_segment(sps, pps, width, height, timescale=90000):
    ftyp = _box(b"ftyp", b"isom", struct.pack(">I", 512), b"isomiso6avc1mp41")
    avcc = _box(b"avcC", bytes([1, sps[1], sps[2], sps[3], 0xFF, 0xE1]), struct.pack(">H", len(sps)), sps, bytes([1]), struct.pack(">H", len(pps)), pps)
    avc1 = _box(b"avc1", bytes(6), struct.pack(">H", 1), bytes(16), struct.pack(">HH", width, height), struct.pack(">II", 0x00480000, 0x00480000), bytes(4),
                struct.pack(">H", 1), bytes(32), struct.pack(">Hh", 0x18, -1), avcc)
    stbl = _box(b"stbl", _full(b"stsd", 0, 0, struct.pack(">I", 1), avc1), _full(b"stts", 0, 0, struct.pack(">I", 0)), _full(b"stsc", 0, 0, struct.pack(">I", 0)),
                _full(b"stsz", 0, 0, struct.pack(">II", 0, 0)), _full(b"stco", 0, 0, struct.pack(">I", 0)))
    dinf = _box(b"dinf", _full(b"dref", 0, 0, struct.pack(">I", 1), _full(b"url ", 0, 1)))
    minf = _box(b"minf", _full(b"vmhd", 0, 1, bytes(8)), dinf, stbl)
    mdhd = _full(b"mdhd", 0, 0, struct.pack(">IIIIHH", 0, 0, timescale, 0, 0x55C4, 0))
    hdlr = _full(b"hdlr", 0, 0, struct.pack(">I", 0), b"vide", bytes(12), b"VideoHandler\0")
    tkhd = _full(b"tkhd", 0, 3, struct.pack(">IIIII", 0, 0, 1, 0, 0), bytes(8), struct.pack(">hhhH", 0, 0, 0, 0), _MATRIX, struct.pack(">II", width << 16, height << 16))
    trak = _box(b"trak", tkhd, _box(b"mdia", mdhd, hdlr, minf))
    mvhd = _full(b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, 1000, 0), struct.pack(">IhH", 0x00010000, 0x0100, 0), bytes(8), _MATRIX, bytes(24), struct.pack(">I", 2))
    mvex = _box(b"mvex", _full(b"trex", 0, 0, struct.pack(">IIIII", 1, 1, 0, 0, 0)))
    return ftyp + _box(b"moov", mvhd, trak, mvex)


def media_segment(seq, decode_time, samples):
    trun_rows = b"".join(struct.pack(">III", dur, len(data), 0x02000000 if key else 0x01010000) for data, dur, key in samples)
    mfhd = _full(b"mfhd", 0, 0, struct.pack(">I", seq))
    tfhd = _full(b"tfhd", 0, 0x020000, struct.pack(">I", 1))
    tfdt = _full(b"tfdt", 1, 0, struct.pack(">Q", decode_time))

    def moof(offset):
        trun = _full(b"trun", 0, 0x701, struct.pack(">Ii", len(samples), offset), trun_rows)
        return _box(b"moof", mfhd, _box(b"traf", tfhd, tfdt, trun))
    size = len(moof(0))
    return moof(size + 8) + _box(b"mdat", b"".join(d for d, _, _ in samples))


class Fmp4:

    def __init__(self, fps=30, width=640, height=360, frames=FRAGMENT_FRAMES):
        self.fps, self.width, self.height, self.frames = fps, width, height, frames
        self.buf = b""
        self.au, self.au_slice = [], False
        self.sps = self.pps = None
        self.started = False
        self.samples = []
        self.seq = 0
        self.t = 0
        self.dur = 90000 // fps

    def feed(self, data):
        self.buf += data
        out = []
        starts = self._starts()
        if len(starts) >= 2:
            for a, b in zip(starts, starts[1:]):
                nal = self.buf[a:b - 3].rstrip(b"\x00")
                out += self._nal(nal)
            self.buf = self.buf[starts[-1] - 3:]
        return out

    def _starts(self):
        i, res = 0, []
        while True:
            j = self.buf.find(b"\x00\x00\x01", i)
            if j < 0:
                return res
            res.append(j + 3)
            i = j + 3

    def _nal(self, nal):
        if not nal:
            return []
        t = nal[0] & 0x1F
        out = []
        if t in (6, 7, 8, 9) and self.au_slice:
            out += self._emit()
        elif t in (1, 5) and self.au_slice and len(nal) > 1 and nal[1] & 0x80:
            out += self._emit()
        if t == 7:
            self.sps = nal
        elif t == 8:
            self.pps = nal
        self.au.append(nal)
        if t in (1, 5):
            self.au_slice = True
        return out

    def _emit(self):
        nals, self.au, self.au_slice = self.au, [], False
        key = any((n[0] & 0x1F) == 5 for n in nals)
        out = []
        if not self.started:
            if not (key and self.sps and self.pps):
                return out
            self.started = True
            out.append(init_segment(self.sps, self.pps, self.width, self.height))
        data = b"".join(struct.pack(">I", len(n)) + n for n in nals if (n[0] & 0x1F) in (1, 5))
        self.samples.append((data, self.dur, key))
        if len(self.samples) >= self.frames:
            self.seq += 1
            out.append(media_segment(self.seq, self.t, self.samples))
            self.t += self.dur * len(self.samples)
            self.samples = []
        return out


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
        return LONG_SECONDS if long else MAX_SECONDS

    def status(self, sending):
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
        try:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(0x0003))
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
            attach_filter(sock, self.port)
            sock.bind(("lo", 0))
            sock.settimeout(0.5)
            return sock
        except (OSError, AttributeError) as e:
            raise Unavailable("capture: %s" % e)

    def spawn(self, fps, width, fmt="mjpeg"):
        try:
            return subprocess.Popen(gst_argv(self.codec(), fps, width, little_cpus(self.big_cpus()), fmt), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, env=dict(os.environ, GST_MPP_NO_RGA="1"), bufsize=0)
        except OSError as e:
            raise Unavailable("gst: %s" % e)

    def stream(self, write, fps, width, still_sending, max_seconds=MAX_SECONDS, ready=None, gone=None, fmt="mjpeg"):
        fps = clamp(fps, *FPS_RANGE, 30)
        width = clamp(width, *WIDTH_RANGE, 640)
        self._acquire()
        sock = proc = None
        stop = threading.Event()
        try:
            sock = self.open_capture()
            proc = self.spawn(fps, width, fmt)
            mux = Fmp4(fps, width - width % 2, (width * 9 // 16) - ((width * 9 // 16) % 2)) if fmt == "mp4" else None
            if ready is not None:
                ready()

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
                try:
                    for chunk in (mux.feed(data) if mux else [data]):
                        first = True
                        write(chunk)
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
    out = {}
    try:
        import fcntl
    except ImportError:
        return out
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for n in names:
            try:
                out[n] = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", n[:15].encode()))[20:24])
            except OSError:
                pass
    finally:
        s.close()
    return out


def default_route_iface(path="/proc/net/route"):
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


class Traffic:
    TX_FACTOR = 1.046
    RX_FACTOR = 0.01

    def __init__(self, addrs=None, default=None):
        self.lock = threading.Lock()
        self.by_ip = {}
        self.addrs, self.default = addrs or iface_addrs, default or default_route_iface

    def add(self, ip, nbytes):
        with self.lock:
            self.by_ip[ip or ""] = self.by_ip.get(ip or "", 0) + nbytes

    def adjust(self, net):
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
    try:
        if not select.select([conn], [], [], 0)[0]:
            return False
        return conn.recv(1, socket.MSG_PEEK) == b""
    except (ValueError, OSError):
        return True


class Service:

    def __init__(self, preview, sending=sending_now, idle=IDLE_EXIT):
        self.pv, self.sending, self.idle = preview, sending, idle
        self.lock = threading.Lock()
        self.active = 0
        self.last = time.monotonic()

    def serve(self, lsock):
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
            fmt = req.get("fmt") if req.get("fmt") in FORMATS else "mp4"
            self.pv.stream(conn.sendall, req.get("fps"), req.get("w"), self.sending, max_seconds=self.pv.limit(req.get("long") is True), gone=lambda: peer_gone(conn), fmt=fmt,
                           ready=lambda: (self._send(conn, {"ok": True}), conn.settimeout(15)))
        except Busy:
            self._send(conn, {"error": "busy"})
        except Unavailable:
            self._send(conn, {"error": "capture"})


class Client:

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

    def open(self, fps, width, long=False, fmt="mp4"):
        s = self._call({"cmd": "stream", "fps": clamp(fps, *FPS_RANGE, 30), "w": clamp(width, *WIDTH_RANGE, 640), "long": bool(long), "fmt": fmt if fmt in FORMATS else "mp4"})
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
