
import re
import socket
import threading
import time
import ssl
import unicodedata

from tw.store import TwitchStore


class _IrcLines:
    MAX = 16384

    def __init__(self, sock, clock):
        self.sock, self.clock, self.buf = sock, clock, b""

    def get(self, until):
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
    HOST, PORT = "irc.chat.twitch.tv", 6697
    MAX_BYTES = 450
    OK = "Gesendet"
    NO_CONNECT = "Keine Verbindung zu Twitch"
    NO_TLS = "Sichere Verbindung zu Twitch nicht möglich (Zertifikat oder Uhrzeit der Box prüfen)"
    LOGIN_FAILED = "Anmeldung fehlgeschlagen (Token ungültig oder ohne Recht zum Schreiben)"
    NO_ANSWER = "Keine Antwort von Twitch (Zeitüberschreitung)"
    CLOSED = "Twitch hat die Verbindung beendet"
    NOT_ALLOWED = "Der Bot darf im Kanal nicht schreiben (z. B. nur Follower)"
    LOGIN_NOTICE_RE = re.compile(r"authentication failed|login unsuccessful|improperly formatted", re.I)

    def __init__(self, host=None, port=None, tls=True, context=None, connect_timeout=10.0, total=15.0, notice_wait=2.0, clock=time.monotonic, paths=None):
        self.paths = paths or ChatPaths()
        self.host, self.port, self.tls, self.context = host or self.HOST, port or self.PORT, tls, context
        self.connect_timeout, self.total, self.notice_wait, self.clock = connect_timeout, total, notice_wait, clock

    @staticmethod
    def clean_text(text):
        t = "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in text).strip()
        bad = ("Cc", "Zl", "Zp", "Cs", "Co", "Cn")
        if not t or any(unicodedata.category(ch) in bad or ch in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" for ch in t) or t[0] in "/.":
            return None
        raw = t.encode("utf-8")
        return t if len(raw) <= TwitchChat.MAX_BYTES else raw[:TwitchChat.MAX_BYTES].decode("utf-8", "ignore").rstrip()

    @staticmethod
    def _parse(line):
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
        text = text.replace(token, "…")
        text = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
        return text[:120] + ("…" if len(text) > 120 else "")

    @staticmethod
    def _pong(sock, rest):
        arg = "".join(ch for ch in rest if ch.isprintable())[:100]
        sock.sendall(b"PONG " + arg.encode("utf-8") + b"\r\n")

    def send(self, login, token, channel, text):
        try:
            return self._send(login, token, channel, text)
        except Exception:
            return False, "unbekannter Fehler"

    def _send(self, login, token, channel, text):
        if not all(isinstance(v, str) for v in (login, token, channel, text)):
            return False, "Ungültige Anfrage"
        login, channel, body = login.lower(), channel.lower(), self.clean_text(text)
        if body is None or not (TwitchStore.NAME_RE.fullmatch(login) and TwitchStore.NAME_RE.fullmatch(channel) and TwitchStore.TOKEN_RE.fullmatch(token)):
            return False, "Ungültige Anfrage"
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
        while True:
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
            while notice is None:
                line = rd.get(end)
                if line is None:
                    break
                cmd, rest = self._parse(line)
                if cmd == "PING":
                    self._pong(sock, rest)
                elif cmd == "NOTICE":
                    notice = self._scrub(self._notice_text(rest), token)
        except EOFError:
            pass
        try:
            sock.sendall(b"QUIT\r\n")
        except OSError:
            pass
        if notice is None:
            return True, self.OK
        return False, self.NOT_ALLOWED + (": " + notice if notice else "")


def _ascii_digits(v):
    return isinstance(v, str) and v.isascii() and v.isdigit()


class ChatPaths:

    def __init__(self, sources=None, clock=time.monotonic, links_file="/run/pipbox-send/srtla-links.txt"):
        self.sources, self.clock, self.links_file = sources, clock, links_file
        self.bad = {}
        self.lock = threading.Lock()

    def _quality(self):
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
        good.sort(key=lambda x: q.get(x, 50000))
        return good + [None] + bad

    def fail(self, src, hold=300.0):
        if src:
            with self.lock:
                self.bad[src] = self.clock() + hold

    def connect(self, host, port, timeout=6.0):
        last = OSError()
        for src in self.order():
            try:
                return socket.create_connection((host, port), timeout=timeout, source_address=(src, 0) if src else None), src
            except socket.gaierror as e:
                raise e
            except OSError as e:
                last = e
                self.fail(src, 120.0)
        raise last

    def request(self, method, url, data=None, headers=None, timeout=15.0, limit=200_000, once=False, prefer=False):
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
                    return 0, b""
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
                            return status, b""
                        raise
                except (OSError, http.client.HTTPException, ValueError):
                    self.fail(src, 120.0)
                    if once:
                        return -1, b""
            except (OSError, http.client.HTTPException, ValueError):
                self.fail(src, 120.0)
            finally:
                try:
                    if conn:
                        conn.close()
                except OSError:
                    pass
        return 0, b""
