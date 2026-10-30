
import base64
import hashlib
import json
import os
import collections
import re
import socket
import threading
import time
import ssl

from tw.irc import ChatPaths
from tw.reader import TwitchReader


class WebSocketLite:
    GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    MAX = 1 << 20

    def __init__(self, sock, clock=time.monotonic):
        self.sock, self.clock = sock, clock
        self.buf = bytearray()
        self.parts, self.first = bytearray(), None
        self.last_rx = clock()

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
        deadline = self.clock() + timeout
        while True:
            if self.clock() > deadline:
                return None
            if not self._need(2, deadline):
                return None
            b0, b1 = self.buf[0], self.buf[1]
            fin, op = b0 & 0x80, b0 & 0x0f
            if (b0 & 0x70) or (b1 & 0x80):
                raise OSError("ws-protocol")
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
    HOST, PORT, PATH = "eventsub.wss.twitch.tv", 443, "/ws?keepalive_timeout_seconds=30"
    SUBS = (("channel.follow", "2", "moderator:read:followers"),
            ("channel.channel_points_custom_reward_redemption.add", "1", "channel:read:redemptions"))
    ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,100}")
    KEEP_MIN = 10.0
    RETRY_FIRST = 5.0
    SUB_DELAYS = (0.0, 5.0, 15.0)

    def __init__(self, store, account, mod, reader, paths=None, host=None, port=None, tls=True, clock=time.monotonic, sleep=time.sleep):
        self.store, self.account, self.mod, self.reader = store, account, mod, reader
        self.paths = paths or getattr(account, "paths", None) or ChatPaths()
        self.host, self.port, self.tls = host or self.HOST, port or self.PORT, tls
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.thread, self.gen, self.sid = None, 0, None
        self.state, self.error, self.types = "aus", "", set()
        self.seen, self.seen_set = collections.deque(maxlen=300), set()

    def granted(self):
        try:
            if not self.account.ready():
                return [], ""
            have = set(self.account.status().get("scopes") or [])
            mine = self.account.login()
            if str((self.store.data or {}).get("channel") or mine).lower() != mine:
                return [], ""
            return [t for t in self.SUBS if t[2] in have], self.account.user_id()
        except Exception:
            return [], ""

    def status(self):
        subs, uid = self.granted()
        return {"state": self.state if subs and uid else "aus", "types": sorted(self.types), "error": self.error if self.state == "fehler" else ""}

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
        m = re.fullmatch(r"wss://([A-Za-z0-9.\-]{1,100})(/[A-Za-z0-9_\-./%]{0,200})?(\?[A-Za-z0-9_\-./=&%]{0,300})?", url or "")
        if not m or not (m.group(1) == (allow or cls.HOST) or m.group(1).endswith(".twitch.tv")):
            raise OSError("ws-bad-url")
        path = (m.group(2) or "") + (m.group(3) or "")
        return m.group(1), path if path.startswith("/") else "/" + path

    def _open(self, host, path):
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
            tok = self.account.token()
            heard, keep, sig = self.clock(), 30.0, None
            while self._alive(gen):
                msg = ws.recv_message(1.0)
                heard = max(heard, ws.last_rx)
                if msg is None:
                    if self.clock() - heard > keep * 1.5:
                        self.paths.fail(src, 120.0)
                        raise OSError("still")
                    if sig is not None and tuple(t[0] for t in self.granted()[0]) != sig:
                        return
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
                    threading.Thread(target=self._subscribe, args=(gen, sid, tok, src), daemon=True).start()
                elif typ == "session_reconnect":
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
                        return
        finally:
            ws.close()

    def _subscribe(self, gen, sid, tok, src):
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
                                   timeout=4.0, token=tok, prefer=src, accept=(409,))
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

    def _notify(self, meta, payload):
        mid = meta.get("message_id")
        if isinstance(mid, str) and mid:
            with self.lock:
                if mid in self.seen_set:
                    return
                if len(self.seen) == self.seen.maxlen:
                    self.seen_set.discard(self.seen[0])
                self.seen.append(mid)
                self.seen_set.add(mid)
        item = self.map_event((payload.get("subscription") or {}).get("type"), payload.get("event"))
        if item:
            self.reader._add(item)

    @staticmethod
    def map_event(typ, ev):
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
