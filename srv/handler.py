
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler
try:
    import pipbox_preview
except ImportError:
    pipbox_preview = None

from srv.access import client_origin
from srv.cameras import HDMI_KEY
from srv.netinfo import uplink_states
from srv.settings import SettingsTransfer
from srv.stats import build_stats_snapshot
from srv.routes_get import GetRoutes
from srv.routes_post import PostRoutes
from srv.static import WebFiles
from srv.status import status_light
from srv.util import NOT_HANDLED, WEB_DIR, read


class Handler(PostRoutes, GetRoutes, BaseHTTPRequestHandler):
    ERR_NO_ACCOUNT = "Zum Schreiben und Moderieren bitte in diesem Browser bei Twitch anmelden (Zuschauer oder Moderator)"
    ERR_NOT_MOD = "Moderieren dürfen nur der Streamer und Moderatoren dieses Kanals"
    timeout = 15
    BODY_SECONDS = 15.0
    REQUEST_SECONDS = 15.0
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
            real = self.ip()
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
    statsshare = None
    statsserver = None
    fpsprobe = None

    def log_message(self, *a):
        pass

    def host(self):
        return (self.headers.get("Host") or "box").split(":")[0]

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

    def origin(self):
        return client_origin(self.client_address[0], bool(self.headers.get("X-Forwarded-For")))

    def local_ip(self):
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
            raise ValueError("Ungültige Anfrage")
        return out

    def read_body(self, n):
        end = time.monotonic() + self.BODY_SECONDS
        buf = b""
        while len(buf) < n:
            left = end - time.monotonic()
            if left <= 0:
                raise ValueError("Die Anfrage kam zu langsam")
            conn = getattr(self, "connection", None)
            if conn is None:
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
        pv = self.preview
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if not pv:
            return self.reply(503, {"error": "missing"})
        org = self.origin()
        pa = self.previewaccess
        if not pa.allowed(org):
            return self.reply(403, {"error": "extern"})
        if not self.send._active():
            return self.reply(503, {"error": "off"})
        if not pa.acquire(org):
            return self.reply(429, {"error": "busy"})
        try:
            return self._preview_stream(pv, q, org)
        finally:
            pa.release(org)

    def _preview_stream(self, pv, q, org):
        try:
            fmt = (q.get("fmt") or ["mp4"])[0]
            fmt = fmt if fmt in pipbox_preview.FORMATS else "mp4"
            fps, width, long = (q.get("fps") or [30])[0], (q.get("w") or [640])[0], (q.get("long") or [""])[0] == "1"
            if org == "extern":
                try:
                    fps = min(int(fps), self.previewaccess.EXT_FPS)
                    width = min(int(width), self.previewaccess.EXT_WIDTH)
                except (TypeError, ValueError):
                    fps, width = self.previewaccess.EXT_FPS, self.previewaccess.EXT_WIDTH
                long = False
            up, head = pv.open(fps, width, long, fmt)
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
                pipbox_preview.TRAFFIC.add(lip, len(data))
        except OSError:
            pass
        finally:
            try:
                up.close()
            except OSError:
                pass
        if not started:
            self.reply(503, {"error": "kein-bild"})

    webfiles = WebFiles()

    def send_bytes(self, code, body, ctype, cookie=None, headers=None, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, e, cache="no-store"):
        gz = e["gz"] is not None and "gzip" in (self.headers.get("Accept-Encoding") or "")
        etag = e["etag_gz"] if gz else e["etag"]
        head = {"ETag": etag, "Vary": "Accept-Encoding"}
        if etag in [x.strip() for x in (self.headers.get("If-None-Match") or "").split(",")]:
            self.send_response(304)
            for k, v in dict(head, **{"Cache-Control": cache, "X-Content-Type-Options": "nosniff"}).items():
                self.send_header(k, v)
            self.end_headers()
            return
        if gz:
            head["Content-Encoding"] = "gzip"
        self.send_bytes(200, e["gz"] if gz else e["body"], e["ctype"], headers=head, cache=cache)

    def reply(self, code, obj, cookie=None):
        self.send_bytes(code, json.dumps(obj).encode(), "application/json", cookie)

    def cookie(self, tok, max_age):
        flags = "HttpOnly; SameSite=Strict; Path=/"
        if self.headers.get("X-Forwarded-Proto") == "https":
            flags += "; Secure"
        return f"pb_session={tok}; Max-Age={max_age}; {flags}"

    I18N_RE = re.compile(r"^/i18n/([a-z]{2,3}|languages)\.json$")

    def i18n_file(self, path):
        if path == "/i18n.js":
            name = "i18n.js"
        else:
            m = self.I18N_RE.match(path)
            if not m:
                return False
            name = "i18n/%s.json" % m.group(1)
        e = self.webfiles.entry(name)
        if e is None:
            self.reply(404, {"error": "not found"})
        else:
            self.send_file(e, "no-cache")
        return True

    def page(self, name):
        e = self.webfiles.entry(name, page=True)
        if e is None:
            return self.send_bytes(200, b"", "text/html; charset=utf-8")
        self.send_file(e)

    def conn_labels(self):
        base = dict(self.wifi.labels()) if self.wifi else {}
        base.update(self.names.conn_names() if self.names else {})
        return base

    def metrics_data(self, light=False):
        m = self.sampler.sample()
        try:
            m["alerts"] = list(m.get("alerts") or []) + self.usbwatch.alerts()
        except Exception:
            pass
        m["cameras"] = self.cams.listing(self.host(), self.djisvc.host_for_key)
        parked = set()
        try:
            pc = self.pipeline.cfg
            inactive = set(pc.get("inactive") or []) if pc.get("type") == "pip" else set()
            parked = inactive | self.djisvc.parked_keys()
            pips = {pc.get(k) for k in ("pip", "pip2", "pip3") if pc.get(k)} if pc.get("type") == "pip" else set()
            for c in m["cameras"]:
                c["parked"] = c.get("key") in parked
                c["is_main"] = pc.get("type") == "pip" and c.get("key") == pc.get("main")
                c["in_pips"] = c.get("key") in pips
        except Exception:
            pass
        try:
            hs = self.hdmi.status() if self.hdmi else None
            m["alerts"] = list(m.get("alerts") or []) + self.outages.alerts(m["cameras"], hs, parked)
        except Exception:
            pass
        extras = self.djisvc.camera_extras()
        for c in m["cameras"]:
            c.update(extras.get(c["key"], {}))
        m["uplinks"] = uplink_states(((self.srtla.data or {}).get("settings") or {}).get("uplinks") or [])
        m["conn_names"] = self.conn_labels()
        pic = None if light else self.send.picture()
        for c in m["cameras"]:
            p = pic.get(c["key"]) if pic else None
            c["pic"], c["pic_wait"] = (p[0], p[1]) if p else (None, 0)
        measured = self.send.measured_fps()
        rates = self.send.measured_rates()
        for c in m["cameras"]:
            if c["key"] in rates:
                c["rates"] = list(rates[c["key"]])
        if self.fpsprobe:
            self.fpsprobe.keep({c["key"] for c in m["cameras"] if c.get("state") == "live"})
        for c in m["cameras"]:
            if c.get("state") == "live" and not c.get("fps"):
                if c["key"] in measured:
                    c["fps"], c["fps_measured"] = measured[c["key"]], True
                    continue
                f = self.djisvc.fps_for_key(c["key"])
                if f:
                    c["fps"], c["fps_set"] = float(f), True
                elif self.fpsprobe and c["key"] != HDMI_KEY:
                    p = self.fpsprobe.get(c["key"])
                    if p:
                        c["fps"], c["fps_measured"] = p, True
        try:
            m["light"] = status_light(m, self.twitch._live(), self.sysalerts.data, self.twitch.store.data.get("threshold", 10), self.uplinkmem, self.fpsmem)
        except Exception:
            m["light"] = None
        return m

    @classmethod
    def light_now(cls):
        h = object.__new__(cls)
        h.headers = {}
        return h.metrics_data(light=True).get("light")

    @classmethod
    def stats_snapshot_now(cls):
        h = object.__new__(cls)
        h.headers = {}
        return cls.statsshare.snapshot(lambda: build_stats_snapshot(h.metrics_data(light=True), {"active": h.send.live_fast()}, h.conn_labels(), h.statsshare.groups()))

    def stats_status(self):
        st = self.statsshare.status()
        rs = self.remote.status()
        st.update(installed=rs["installed"], connected=rs["connected"], busy=rs["state"] == "working", state=rs["state"], message=rs["message"], hint_url=rs["hint_url"],
                  public=bool(rs.get("stats")), clients=self.statsserver.clients() if self.statsserver else 0, page_url="", ws_url="")
        base = rs.get("stats_base") or ""
        if st["enabled"] and st["token"] and base.startswith("https://"):
            st["page_url"] = base + "/?t=" + st["token"]
            st["ws_url"] = "wss://" + base[len("https://"):] + "/ws?t=" + st["token"]
        return st

    def stats_widget(self):
        st = self.stats_status()
        if not st["ws_url"]:
            return self.reply(409, {"error": "Die öffentliche Statistik ist nicht freigegeben"})
        page = read(os.path.join(WEB_DIR, "statistik.html"), None)
        if page is None:
            return self.reply(404, {"error": "statistik.html fehlt"})
        body = page.replace("__STATS_URL__", st["ws_url"]).encode("utf-8")
        return self.send_bytes(200, body, "text/html; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="statistik.html"'})

    def phone_view_wanted(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, val = part.strip().partition("=")
            if k == "pb_view_mode" and val == "mobile":
                return True
        return False

    def do_GET(self):
        self.request_read()
        path = self.path.split("?")[0]
        if self.get_public(path) is not NOT_HANDLED:
            return
        if not self.authed():
            return self.reply(401, {"error": "nicht angemeldet"})
        for part in (self.get_status, self.get_admin, self.get_system, self.get_chat, self.get_sending):
            if part(path) is not NOT_HANDLED:
                return
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            d = self.read_json(SettingsTransfer.MAX_BODY if path.startswith("/api/settings/") and self.authed() else 4096)
            if self.post_public(path, d) is not NOT_HANDLED:
                return
            if not self.authed():
                return self.reply(401, {"error": "nicht angemeldet"})
            for part in (self.post_sending, self.post_admin, self.post_picture, self.post_access, self.post_chat, self.post_cameras):
                if part(path, d) is not NOT_HANDLED:
                    return
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
            self.hdmi.on_camera_removed()
        if cam:
            self.cams.forget(cam.get("key"))
            if getattr(self, "djisvc", None):
                self.djisvc.remove_for_key(cam.get("key"))
        self.reply(200, {"ok": True})
