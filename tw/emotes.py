
import base64
import json
import os
import collections
import re
import socket
import struct
import threading
import time
import urllib.request
import ssl


class ThirdPartyEmotes:
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
        self.users, self.uq, self.uthread, self.on_user = collections.OrderedDict(), collections.deque(), None, None
        self.personal = collections.OrderedDict()
        self.sthread, self.stouch, self.sroom, self.sretry, self.sfails = None, 0.0, "", 0.0, 0
        self.sock_on = paths is not None and fetch is None
        self.sock_host, self.sock_port, self.sock_tls, self.sock_path = "sockets.betterttv.net", 443, True, "/ws"

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
        self._watch(room)
        with self.lock:
            if (self.thread is not None and self.thread.is_alive()) or (self.room == room and self.clock() < self.until):
                return
            self.room = room
            self.until = self.clock() + self.RETRY
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
                fails += 1
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
                self.map = out
            else:
                merged = dict(self.map)
                merged.update(out)
                self.map = merged
            self.until = self.clock() + (self.TTL if fails == 0 else self.RETRY)

    USER_TTL, USER_MAX, USER_QUEUE = 1800.0, 300, 50

    SOCK_IDLE, SOCK_MAX_FRAME = 900.0, 1_000_000

    def _watch(self, room):
        if not room or not self.sock_on:
            return
        with self.lock:
            self.stouch, self.sroom = self.clock(), room
            if (self.sthread is not None and self.sthread.is_alive()) or self.clock() < self.sretry:
                return
            self.sthread = threading.Thread(target=self._sock_loop, args=(room,), daemon=True)
            self.sthread.start()

    def _sock_open(self):
        if self.paths is not None:
            sk, _ = self.paths.connect(self.sock_host, self.sock_port, timeout=8)
        else:
            sk = socket.create_connection((self.sock_host, self.sock_port), timeout=8)
        try:
            if self.sock_tls:
                sk = ssl.create_default_context().wrap_socket(sk, server_hostname=self.sock_host)
            key = base64.b64encode(os.urandom(16)).decode()
            sk.sendall((b"GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                        b"Sec-WebSocket-Version: 13\r\nUser-Agent: pipbox\r\n\r\n") % (self.sock_path.encode(), self.sock_host.encode(), key.encode()))
            sk.settimeout(8)
            buf = b""
            while b"\r\n\r\n" not in buf:
                d = sk.recv(4096)
                if not d or len(buf) > 8192:
                    raise OSError("ws-no-answer")
                buf += d
            head, _, rest = buf.partition(b"\r\n\r\n")
            if b" 101 " not in head.split(b"\r\n")[0] + b" ":
                raise OSError("ws-no-upgrade")
            return sk, rest
        except Exception:
            try:
                sk.close()
            except Exception:
                pass
            raise

    @staticmethod
    def _ws_frame(opcode, payload=b""):
        m = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode, 0x80 | (n if n < 126 else 126)]) + (struct.pack(">H", n) if n >= 126 else b"")
        return head + m + bytes(b ^ m[i % 4] for i, b in enumerate(payload))

    def _ws_frames(self, buf):
        out = []
        while len(buf) >= 2:
            op, ln, off = buf[0] & 0x0F, buf[1] & 0x7F, 2
            if ln == 126:
                if len(buf) < 4:
                    break
                ln, off = struct.unpack(">H", buf[2:4])[0], 4
            elif ln == 127:
                if len(buf) < 10:
                    break
                ln, off = struct.unpack(">Q", buf[2:10])[0], 10
            if ln > self.SOCK_MAX_FRAME:
                raise ValueError("ws-frame-too-large")
            if buf[1] & 0x80:
                off += 4
            if len(buf) < off + ln:
                break
            out.append((op, buf[off:off + ln]))
            buf = buf[off + ln:]
        return out, buf

    def _sock_loop(self, room):
        sk = None
        try:
            sk, buf = self._sock_open()
            sk.sendall(self._ws_frame(1, json.dumps({"name": "join_channel", "data": {"name": "twitch:" + room}}).encode()))
            sk.settimeout(5)
            last_ping = self.clock()
            while self.clock() - self.stouch < self.SOCK_IDLE and self.sroom == room:
                frames, buf = self._ws_frames(buf)
                for op, data in frames:
                    if op == 1:
                        self._on_event(data.decode("utf-8", "replace"))
                    elif op == 9:
                        sk.sendall(self._ws_frame(10, data[:125]))
                    elif op == 8:
                        raise OSError("ws-closed-by-peer")
                if self.clock() - last_ping > 25:
                    sk.sendall(self._ws_frame(9, b"pb"))
                    last_ping = self.clock()
                try:
                    d = sk.recv(65536)
                except socket.timeout:
                    continue
                if not d:
                    raise OSError("ws-closed")
                buf += d
                if len(buf) > 2 * self.SOCK_MAX_FRAME:
                    raise ValueError("ws-too-much-data")
            self.sfails = 0
        except Exception:
            self.sfails = min(self.sfails + 1, 6)
            with self.lock:
                self.sretry = self.clock() + min(30.0 * 2 ** (self.sfails - 1), 900.0)
        finally:
            if sk is not None:
                try:
                    sk.close()
                except Exception:
                    pass
            with self.lock:
                self.sthread = None

    def _on_event(self, text):
        try:
            d = json.loads(text)
        except ValueError:
            return
        if not isinstance(d, dict) or d.get("name") != "lookup_user" or not isinstance(d.get("data"), dict):
            return
        data = d["data"]
        uid = str(data.get("providerId") or "")
        if not re.fullmatch(r"[0-9]{1,12}", uid) or not data.get("pro") or not isinstance(data.get("emotes"), list):
            return
        out = {}
        self._add(out, "bttv", data["emotes"], "id", "code", self.BTTV_ID)
        if not out:
            return
        with self.lock:
            self.personal[uid] = (self.clock() + self.USER_TTL, out)
            self.personal.move_to_end(uid)
            while len(self.personal) > self.USER_MAX:
                self.personal.popitem(last=False)
        cb = self.on_user
        if cb:
            try:
                cb(uid)
            except Exception:
                pass

    def user_map(self, uid):
        with self.lock:
            now = self.clock()
            e, p = self.users.get(uid), self.personal.get(uid)
            base = e[1] if e and now < e[0] else None
            per = p[1] if p and now < p[0] else None
        if per is None:
            return base
        return {**(base or {}), **per}

    def want_user(self, uid):
        if not (isinstance(uid, str) and re.fullmatch(r"[0-9]{1,12}", uid)):
            return
        with self.lock:
            e = self.users.get(uid)
            if (e and self.clock() < e[0]) or uid in self.uq or len(self.uq) >= self.USER_QUEUE:
                return
            self.uq.append(uid)
            if self.uthread is None or not self.uthread.is_alive():
                self.uthread = threading.Thread(target=self._user_loop, daemon=True)
                self.uthread.start()

    def _user_loop(self):
        while True:
            with self.lock:
                if not self.uq:
                    self.uthread = None
                    return
                uid = self.uq.popleft()
            out, fails = {}, 0
            for kind, url in (("bttv", "https://api.betterttv.net/3/cached/users/twitch/" + uid), ("7tv", "https://7tv.io/v3/users/twitch/" + uid)):
                try:
                    d = self.fetch(url)
                except Exception:
                    d = None
                if d is None:
                    fails += 1
                    continue
                d = d if isinstance(d, dict) else {}
                if kind == "bttv":
                    lst = [x for k in ("channelEmotes", "sharedEmotes") for x in (d.get(k) if isinstance(d.get(k), list) else [])]
                    self._add(out, "bttv", lst, "id", "code", self.BTTV_ID)
                else:
                    st = d.get("emote_set") if isinstance(d.get("emote_set"), dict) else {}
                    self._add(out, "7tv", st.get("emotes") if isinstance(st.get("emotes"), list) else [], "id", "name", self.STV_ID)
            with self.lock:
                self.users[uid] = (self.clock() + (self.USER_TTL if fails == 0 else self.RETRY), out)
                self.users.move_to_end(uid)
                while len(self.users) > self.USER_MAX:
                    self.users.popitem(last=False)
            cb = self.on_user
            if cb:
                try:
                    cb(uid)
                except Exception:
                    pass

    @staticmethod
    def _apply(item, m):
        text = item.get("text") or ""
        if not m or not text:
            return False
        found = [[m[w.group()], w.start(), w.end() - 1] for w in re.finditer(r"\S+", text) if w.group() in m]
        if not found:
            return False
        merged = [list(e) for e in item.get("emotes") or []]
        n0 = len(merged)
        for e in found:
            if all(e[2] < o[1] or e[1] > o[2] for o in merged):
                merged.append(e)
        item["emotes"] = sorted(merged, key=lambda e: e[1])[:60]
        return len(merged) != n0

    def mark(self, item):
        self._apply(item, self.map)
        uid = item.get("uid")
        if uid:
            self.want_user(uid)
            um = self.user_map(uid)
            if um:
                self._apply(item, um)

    def mark_user(self, item):
        um = self.user_map(item.get("uid") or "")
        return bool(um) and self._apply(item, um)
