
import base64
import hashlib
import ipaddress
import hmac
import json
import os
import collections
import re
import secrets
import select
import struct
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from srv.cameras import KEY_RE
from srv.netinfo import net_label
from srv.util import WEB_DIR, read


class StatsServer:
    HOST, PORT = "127.0.0.1", 8781
    MAX_CLIENTS = 3
    GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    MAX_FRAME = 1024

    def __init__(self, share, snapshot, host=None, port=None):
        self.share, self.snapshot = share, snapshot
        self.host, self.port = host or self.HOST, self.PORT if port is None else port
        self.lock = threading.Lock()
        self.n = 0
        self.httpd = None

    def clients(self):
        with self.lock:
            return self.n

    def _acquire(self):
        with self.lock:
            if self.n >= self.MAX_CLIENTS:
                return False
            self.n += 1
            return True

    def _release(self):
        with self.lock:
            self.n = max(0, self.n - 1)

    @staticmethod
    def frame(op, payload=b""):
        n = len(payload)
        head = bytes([0x80 | op, n]) if n < 126 else bytes([0x80 | op, 126]) + struct.pack(">H", n)
        return head + payload

    @classmethod
    def read_frame(cls, buf):
        if len(buf) < 2:
            return None
        b0, b1 = buf[0], buf[1]
        n, pos = b1 & 0x7f, 2
        if n == 126:
            if len(buf) < 4:
                return None
            n, pos = struct.unpack(">H", bytes(buf[2:4]))[0], 4
        elif n == 127:
            raise ValueError("zu groß")
        if not b1 & 0x80:
            raise ValueError("nicht maskiert")
        if n > cls.MAX_FRAME or not b0 & 0x80:
            raise ValueError("zu groß")
        if len(buf) < pos + 4 + n:
            return None
        mask = buf[pos:pos + 4]
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(buf[pos + 4:pos + 4 + n]))
        del buf[:pos + 4 + n]
        return b0 & 0x0f, data

    def serve_ws(self, sock, every):
        buf = bytearray()
        next_t = time.monotonic()
        while True:
            now = time.monotonic()
            if now >= next_t:
                if not self.share.status()["enabled"]:
                    sock.sendall(self.frame(8, struct.pack(">H", 1001)))
                    return
                sock.settimeout(5)
                sock.sendall(self.frame(1, json.dumps(self.snapshot(), separators=(",", ":")).encode()))
                next_t = max(next_t + every, now + 0.2)
            try:
                r, _, _ = select.select([sock], [], [], max(0.05, next_t - time.monotonic()))
                if r:
                    chunk = sock.recv(4096)
                    if not chunk:
                        return
                    buf += chunk
                    while True:
                        fr = self.read_frame(buf)
                        if fr is None:
                            break
                        op, data = fr
                        if op == 8:
                            sock.sendall(self.frame(8, data[:2]))
                            return
                        if op == 9:
                            sock.sendall(self.frame(10, data[:125]))
            except ValueError:
                try:
                    sock.sendall(self.frame(8, struct.pack(">H", 1002)))
                except OSError:
                    pass
                return

    def make_handler(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            timeout = 15

            def log_message(self, *a):
                pass

            def ip(self):
                peer = self.client_address[0]
                if peer in ("127.0.0.1", "::1"):
                    last = (self.headers.get("X-Forwarded-For", "") or "").split(",")[-1].strip()
                    try:
                        ipaddress.ip_address(last)
                        return last
                    except ValueError:
                        pass
                return peer

            def out(self, code, body, ctype="application/json", extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Connection", "close")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = urllib.parse.parse_qs(u.query)
                if u.path == "/health":
                    return self.out(200, b"ok", "text/plain")
                if u.path in ("/", "/statistik.html"):
                    page = read(os.path.join(WEB_DIR, "statistik.html"), None)
                    if page is None:
                        return self.out(404, b'{"error": "not found"}')
                    return self.out(200, page.replace("__STATS_URL__", "").encode("utf-8"), "text/html; charset=utf-8")
                if u.path not in ("/ws", "/stats"):
                    return self.out(404, b'{"error": "not found"}')
                res = outer.share.check((q.get("t") or [""])[0][:200], self.ip())
                if res == "limit":
                    return self.out(429, b'{"error": "zu viele Versuche"}', extra={"Retry-After": "30"})
                if res != "ok":
                    return self.out(404, b'{"error": "not found"}')
                if u.path == "/stats":
                    return self.out(200, json.dumps(outer.snapshot(), separators=(",", ":")).encode())
                key = (self.headers.get("Sec-WebSocket-Key") or "").strip()
                if "websocket" not in (self.headers.get("Upgrade") or "").lower() or not key or len(key) > 64:
                    return self.out(400, b'{"error": "WebSocket erwartet"}')
                if not outer._acquire():
                    return self.out(503, b'{"error": "zu viele Betrachter"}', extra={"Retry-After": "10"})
                try:
                    every = 1
                    try:
                        every = max(1, min(10, int((q.get("every") or ["1"])[0])))
                    except ValueError:
                        pass
                    accept = base64.b64encode(hashlib.sha1(key.encode() + outer.GUID).digest())
                    self.wfile.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept
                                     + b"\r\nAccess-Control-Allow-Origin: *\r\n\r\n")
                    self.wfile.flush()
                    outer.serve_ws(self.connection, every)
                except OSError:
                    pass
                finally:
                    outer._release()
                    self.close_connection = True

        return H

    def start(self):
        class Srv(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True
        try:
            self.httpd = Srv((self.host, self.port), self.make_handler())
        except OSError as e:
            print("Statistik-Server:", e)
            return False
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return True

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


class StatsShare:
    GROUPS = ("send", "net", "cpu", "temp", "cameras", "alerts", "cores", "disk")
    WINDOW_S, MAX_HITS = 10.0, 40
    FAIL_WINDOW_S, MAX_FAILS = 60.0, 10
    CACHE_S = 1.0
    MAX_IPS = 500

    def __init__(self, state_dir, demo=False):
        self.path = os.path.join(state_dir, "stats-share.json")
        self.token_path = os.path.join(state_dir, "stats-token")
        self.demo = demo
        self.lock = threading.Lock()
        self.build_lock = threading.Lock()
        self.hits, self.fails = {}, {}
        self.cache = None
        self.data = {"enabled": False, "token": "", "groups": {g: True for g in self.GROUPS}}
        try:
            with open(self.path) as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.data["enabled"] = d.get("enabled") is True
                tok = d.get("token")
                self.data["token"] = tok if isinstance(tok, str) and re.match(r"^[A-Za-z0-9_-]{20,64}$", tok) else ""
                g = d.get("groups") if isinstance(d.get("groups"), dict) else {}
                self.data["groups"] = {k: g.get(k) is not False for k in self.GROUPS}
        except (OSError, ValueError):
            pass
        if not self.data["token"]:
            try:
                with open(self.token_path) as f:
                    tok = f.read().strip()
                if re.match(r"^[A-Za-z0-9_-]{20,64}$", tok):
                    self.data["token"] = tok
            except OSError:
                pass
        elif not os.path.exists(self.token_path):
            try:
                self._save_token()
            except OSError:
                pass

    def _save_token(self):
        if not self.data["token"]:
            return
        tmp = self.token_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(self.data["token"])
        os.replace(tmp, self.token_path)

    def _save(self):
        self._save_token()
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def status(self):
        with self.lock:
            return {"enabled": self.data["enabled"], "token": self.data["token"], "groups": dict(self.data["groups"])}

    def groups(self):
        with self.lock:
            return dict(self.data["groups"])

    def update(self, enabled=None, groups=None, new_token=False):
        with self.lock:
            if enabled is not None:
                if not isinstance(enabled, bool):
                    raise ValueError("enabled muss wahr oder falsch sein")
                self.data["enabled"] = enabled
            if groups is not None:
                if not isinstance(groups, dict) or any(k not in self.GROUPS or not isinstance(v, bool) for k, v in groups.items()):
                    raise ValueError("Unbekannte Gruppe")
                self.data["groups"].update(groups)
            if new_token or (self.data["enabled"] and not self.data["token"]):
                self.data["token"] = secrets.token_urlsafe(24)
            self.cache = None
            self._save()
        return self.status()

    def check(self, token, ip):
        now = time.monotonic()
        with self.lock:
            if len(self.hits) > self.MAX_IPS:
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < self.WINDOW_S}
                self.fails = {k: v for k, v in self.fails.items() if v and now - v[-1] < self.FAIL_WINDOW_S}
            h = self.hits.setdefault(ip, collections.deque())
            while h and now - h[0] > self.WINDOW_S:
                h.popleft()
            f = self.fails.setdefault(ip, collections.deque())
            while f and now - f[0] > self.FAIL_WINDOW_S:
                f.popleft()
            if len(h) >= self.MAX_HITS or len(f) >= self.MAX_FAILS:
                return "limit"
            h.append(now)
            tok = self.data["token"]
            if self.data["enabled"] and tok and isinstance(token, str) and hmac.compare_digest(token.encode(), tok.encode()):
                return "ok"
            f.append(now)
            return "bad"

    def snapshot(self, build):
        with self.build_lock:
            now = time.monotonic()
            c = self.cache
            if c and now - c[0] < self.CACHE_S:
                return c[1]
            snap = build()
            self.cache = (now, snap)
            return snap


def build_stats_snapshot(m, send, labels, groups, now=None):
    def num(v, nd=1):
        return round(v, nd) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    def text(v, n=60):
        return str(v)[:n] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""

    m = m if isinstance(m, dict) else {}
    send = send if isinstance(send, dict) else {}
    det = m.get("details") if isinstance(m.get("details"), dict) else {}
    out = {"v": 1, "time": int(time.time() if now is None else now), "live": bool(send.get("active"))}
    if groups.get("send"):
        ss = det.get("send") if isinstance(det.get("send"), dict) else {}
        rates0 = m.get("net") if isinstance(m.get("net"), dict) else {}
        ups0 = m.get("uplinks") if isinstance(m.get("uplinks"), dict) else {}
        wire = sum((r.get("tx_mbit") or 0) for n, r in rates0.items() if isinstance(r, dict) and n in ups0)
        if not ups0:
            wire = sum((r.get("tx_mbit") or 0) for n, r in rates0.items() if isinstance(r, dict) and n != "lo" and not n.startswith(("tailscale", "docker", "veth")))
        pay = m.get("payload_mbit")
        shown = min(pay, wire) if isinstance(pay, (int, float)) and not isinstance(pay, bool) else wire
        out["send"] = {"fps": num(ss.get("fps")), "bitrate_kbps": num(ss.get("bitrate_kbps"), 0), "rtt_ms": num(ss.get("rtt_ms"), 0), "send_mbps": num(ss.get("send_mbps"), 2),
                       "buffer_ms": num(ss.get("snd_buf_ms"), 0), "loss_pct": num(ss.get("loss_pct"), 2),
                       "payload_mbit": num(m.get("payload_mbit"), 2), "shown_mbit": num(shown, 2) if ss else None, "buf_pkts": num(ss.get("snd_buf_pkts"), 0),
                       "loss_total": num(ss.get("loss_total"), 0), "drop_total": num(ss.get("drop_total"), 0),
                       "sent_total": num(ss.get("sent_total"), 0)}
    if groups.get("net"):
        ups = m.get("uplinks") if isinstance(m.get("uplinks"), dict) else {}
        rates = m.get("net") if isinstance(m.get("net"), dict) else {}
        names = labels if isinstance(labels, dict) else {}
        rows = []
        for n, r in rates.items():
            if not isinstance(r, dict) or n == "lo" or n.startswith(("tailscale", "docker", "veth")):
                continue
            u = ups.get(n) if isinstance(ups.get(n), dict) else None
            rows.append({"id": text(n, 20), "name": text(names.get(n) or net_label(n), 40), "rx_mbit": num(r.get("rx_mbit"), 2), "tx_mbit": num(r.get("tx_mbit"), 2),
                         "uplink": u is not None, "state": text(u.get("state"), 10) if u else "", "srtt_ms": num(u.get("srtt"), 0) if u else None})
        rows.sort(key=lambda r: (not r["uplink"], r["id"]))
        out["net"] = rows
        out["uplink_tx_mbit"] = num(sum(r["tx_mbit"] or 0 for r in rows if r["uplink"]), 2)
    if groups.get("cpu"):
        cpu = m.get("cpu") if isinstance(m.get("cpu"), dict) else {}
        mem = m.get("mem") if isinstance(m.get("mem"), dict) else {}
        out["cpu"] = {"avg": num(cpu.get("avg")), "max": num(cpu.get("max")), "mem_used_pct": num(mem.get("used_pct")), "mem_avail_mb": num(mem.get("avail_mb"), 0),
                      "mem_total_mb": num(mem.get("total_mb"), 0)}
    if groups.get("cores"):
        cpu = m.get("cpu") if isinstance(m.get("cpu"), dict) else {}
        acc = det.get("accel") if isinstance(det.get("accel"), dict) else {}

        def accel(k):
            v = acc.get(k)
            return {"load_pct": num(v.get("load_pct"), 0), "mhz": num(v.get("mhz"), 0),
                    "cores": [num(c, 0) for c in (v.get("cores") or [])[:8]] if isinstance(v.get("cores"), list) else None} if isinstance(v, dict) else None
        out["cores"] = {"load": [num(c) for c in (cpu.get("cores") or [])[:16]], "freq_mhz": [num(f, 0) for f in (cpu.get("freq_mhz") or [])[:16]],
                        "gpu": accel("gpu"), "npu": accel("npu")}
    if groups.get("disk"):
        k = det.get("disk")
        out["disk"] = {x: num(k.get(x), 1) for x in ("used_gb", "free_gb", "total_gb", "used_pct")} if isinstance(k, dict) else None
    if groups.get("temp"):
        zones = det.get("temps") if isinstance(det.get("temps"), list) else []
        out["temp"] = {"max_c": num(m.get("temp_c")), "fan_pct": num(m.get("fan_pct"), 0),
                       "zones": [{"name": text(z.get("name"), 30), "c": num(z.get("c"))} for z in zones[:12] if isinstance(z, dict)]}
    if groups.get("cameras"):
        cams = []
        for c in (m.get("cameras") if isinstance(m.get("cameras"), list) else [])[:12]:
            if not isinstance(c, dict):
                continue
            cams.append({"name": text(c.get("name"), 40), "state": text(c.get("state"), 10), "fps": num(c.get("fps")), "mbit": num(c.get("mbit"), 2),
                         "battery": num(c.get("battery"), 0), "charging": c.get("charging") is True, "main": c.get("is_main") is True, "parked": c.get("parked") is True})
        out["cameras"] = cams
    if groups.get("alerts"):
        out["alerts"] = [{"level": text(a.get("level"), 8), "text": text(a.get("text"), 160)} for a in (m.get("alerts") or [])[:10] if isinstance(a, dict)]
    return out


class FpsProbe:
    TTL_S, FAIL_TTL_S, WINDOW_S = 120.0, 20.0, 6.0
    COMMON = (24, 25, 30, 50, 60)
    RE_AVG = re.compile(r"rendered: (\d+), dropped: \d+, current: ([\d.]+), average: ([\d.]+)")

    def __init__(self, base="rtmp://127.0.0.1:1935/publish", registry="", demo=False, runner=None):
        self.base, self.registry, self.demo = base, registry, demo
        self.runner = runner or self._gst
        self.lock = threading.Lock()
        self.cache = {}
        self.busy = False

    @classmethod
    def snap(cls, fps):
        for c in cls.COMMON:
            if abs(fps - c) <= 0.04 * c:
                return float(c)
        return round(fps, 1)

    @classmethod
    def parse(cls, out):
        found = [(int(f), float(c)) for f, c, a in cls.RE_AVG.findall(out or "")]
        if len(found) < 2 or found[-1][0] < 45:
            return None
        cur = [c for f, c in found[1:] if c > 0]
        return cls.snap(sum(cur) / len(cur)) if cur else None

    def _gst(self, key):
        cmd = ["gst-launch-1.0", "-v", "rtmpsrc", "location=%s/%s" % (self.base, key), "!", "flvdemux", "name=d", "d.video", "!", "queue", "!", "h264parse", "!",
               "fpsdisplaysink", "text-overlay=false", "video-sink=fakesink", "fps-update-interval=1000", "signal-fps-measurements=true"]
        env = dict(os.environ)
        if self.registry:
            env["GST_REGISTRY"] = self.registry
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        try:
            out, _ = p.communicate(timeout=self.WINDOW_S + 1.0)
        except subprocess.TimeoutExpired:
            p.kill()
            out, _ = p.communicate()
        return out

    def _run(self, key):
        fps = None
        try:
            fps = self.parse(self.runner(key))
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            print("Bildrate-Messung von %s: %r" % (key, e), flush=True)
            fps = None
        finally:
            with self.lock:
                now = time.monotonic()
                old = self.cache.get(key)
                if fps is None and old and old[1] is not None and now - old[0] < 3 * self.TTL_S:
                    fps = old[1]
                self.cache[key] = (now, fps)
                self.busy = False

    def get(self, key):
        if self.demo or not isinstance(key, str) or not KEY_RE.match(key):
            return None
        now = time.monotonic()
        with self.lock:
            hit = self.cache.get(key)
            ttl = self.TTL_S if hit and hit[1] is not None else self.FAIL_TTL_S
            if (hit is None or now - hit[0] >= ttl) and not self.busy:
                self.busy = True
                threading.Thread(target=self._run, args=(key,), daemon=True).start()
            return hit[1] if hit else None

    def keep(self, live_keys):
        with self.lock:
            for k in [k for k in self.cache if k not in live_keys]:
                del self.cache[k]
