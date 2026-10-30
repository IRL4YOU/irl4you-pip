
import collections
import random
import re
import threading
import time
import ssl

from tw.emotes import ThirdPartyEmotes
from tw.irc import ChatPaths, TwitchChat, _IrcLines, _ascii_digits


class TwitchReader:
    HOST, PORT = "irc.chat.twitch.tv", 6697
    KEEP = 300
    IDLE = 120.0
    COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
    MID_RE = re.compile(r"[0-9a-fA-F-]{20,40}")
    BADGES = ("broadcaster", "moderator", "vip", "subscriber", "founder", "staff", "partner")
    EMOTE_ID_RE = re.compile(r"[A-Za-z0-9_]{1,64}")
    NOTICE_KINDS = {"sub": "sub", "resub": "sub", "subgift": "sub", "submysterygift": "sub", "giftpaidupgrade": "sub", "anongiftpaidupgrade": "sub",
                    "raid": "raid", "announcement": "notice", "ritual": "notice"}
    UNESC = {"s": " ", ":": ";", "\\": "\\", "r": "", "n": ""}

    STALE = 100.0
    PROBE = 40.0

    def __init__(self, store, demo=False, host=None, port=None, tls=True, context=None, clock=time.monotonic, wall=time.time, sleep=time.sleep, paths=None, third=None):
        self.paths = paths or ChatPaths()
        self.third = third or ThirdPartyEmotes(self.paths)
        if hasattr(self.third, "on_user"):
            self.third.on_user = self._user_ready
        self.events = None
        self.store, self.demo = store, demo
        self.host, self.port, self.tls, self.context = host or self.HOST, port or self.PORT, tls, context
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self.lock = threading.Lock()
        self.items = collections.deque(maxlen=self.KEEP)
        self.next_id = 1
        self.gen = 0
        self.state = "aus"
        self.channel = ""
        self.thread = None
        self.last_poll = 0.0
        self.demo_at = 0.0

    @classmethod
    def _tags(cls, raw):
        out = {}
        for part in raw.split(";"):
            k, _, v = part.partition("=")
            if k and len(out) < 60:
                out[k] = re.sub(r"\\(.)", lambda m: cls.UNESC.get(m.group(1), m.group(1)), v)[:600]
        return out

    @staticmethod
    def _clean_map(text, limit):
        out, mp, pend = [], [-1] * len(text), False
        for i, ch in enumerate(text):
            if not ch.isprintable() or ch.isspace():
                pend = bool(out)
                continue
            if pend:
                out.append(" ")
                pend = False
            if len(out) < limit:
                mp[i] = len(out)
                out.append(ch)
        return "".join(out)[:limit], mp

    @staticmethod
    def _remap(emotes, mp):
        out = []
        for eid, a, b in emotes:
            kept = [mp[i] for i in range(a, min(b, len(mp) - 1) + 1) if mp[i] >= 0]
            if kept:
                out.append([eid, kept[0], kept[-1]])
        return out

    @staticmethod
    def _clean(text, limit):
        t = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
        return t[:limit]

    @classmethod
    def _emotes(cls, raw, length):
        out = []
        for chunk in raw.split("/")[:30]:
            eid, _, ranges = chunk.partition(":")
            if not cls.EMOTE_ID_RE.fullmatch(eid):
                continue
            for r in ranges.split(",")[:40]:
                a, _, b = r.partition("-")
                if _ascii_digits(a) and _ascii_digits(b) and int(a) <= int(b) < length:
                    out.append([eid, int(a), int(b)])
        return sorted(out, key=lambda e: e[1])

    def parse(self, line):
        tags = {}
        if line.startswith("@"):
            raw, _, line = line.partition(" ")
            tags = self._tags(raw[1:])
        prefix = ""
        if line.startswith(":"):
            prefix, _, line = line.partition(" ")
        cmd, _, rest = line.partition(" ")
        cmd = cmd.upper()
        if cmd == "CLEARMSG":
            mid = tags.get("target-msg-id", "")
            return {"type": "del", "meta": True, "mid": mid, "text": "", "emotes": []} if self.MID_RE.fullmatch(mid) else None
        if cmd == "CLEARCHAT":
            uid = tags.get("target-user-id", "")
            dur = tags.get("ban-duration", "")
            return {"type": "clear", "meta": True, "uid": uid if _ascii_digits(uid) and len(uid) <= 20 else "",
                    "seconds": int(dur) if _ascii_digits(dur) and len(dur) <= 8 else 0, "text": "", "emotes": []}
        if cmd not in ("PRIVMSG", "USERNOTICE"):
            return None
        _, _, msg = rest.partition(" :")
        nick = prefix[1:].partition("!")[0]
        name = self._clean(tags.get("display-name") or nick, 40)
        color = tags.get("color", "")
        color = color if self.COLOR_RE.fullmatch(color) else ""
        kind, text = "msg", msg
        if cmd == "PRIVMSG" and text.startswith("\x01ACTION ") and text.endswith("\x01"):
            kind, text = "me", text[8:-1]
        native = self._emotes(tags.get("emotes", ""), len(text))
        text, mp = self._clean_map(text, 500)
        item = {"type": kind, "name": name, "color": color, "text": text, "emotes": self._remap(native, mp)}
        if cmd == "USERNOTICE":
            item["type"] = self.NOTICE_KINDS.get(tags.get("msg-id", ""), "notice")
            item["name"], item["emotes"] = "", []
            system = self._clean(tags.get("system-msg", ""), 300)
            item["text"] = (system + (" – " + text if text else "")) or text
            item["color"] = ""
        bits = tags.get("bits", "")
        if _ascii_digits(bits):
            item["bits"] = min(int(bits), 10_000_000)
        ev = self._event(cmd, tags, item)
        if ev is False:
            return None
        if ev:
            item["ev"] = ev
        if cmd == "PRIVMSG":
            if self.MID_RE.fullmatch(tags.get("id", "")):
                item["mid"] = tags["id"]
            uid = tags.get("user-id", "")
            if _ascii_digits(uid) and len(uid) <= 20:
                item["uid"] = uid
            item["login"] = self._clean(nick, 25).lower()
            names = [b.partition("/")[0] for b in tags.get("badges", "").split(",")[:20]]
            item["badges"] = [b for b in self.BADGES if b in names]
        if item["text"] and cmd == "PRIVMSG":
            self.third.ensure(tags.get("room-id", ""))
            self.third.mark(item)
        sent = tags.get("tmi-sent-ts", "")
        if _ascii_digits(sent) and len(sent) <= 13:
            item["ts"] = int(sent) // 1000
        return item if item["text"] else None

    @staticmethod
    def _num(v, limit=10_000_000):
        return min(int(v), limit) if isinstance(v, str) and _ascii_digits(v) and len(v) <= 9 else 0

    @classmethod
    def _event(cls, cmd, tags, item):
        if cmd == "PRIVMSG":
            return {"k": "cheer", "n": item["bits"]} if item.get("bits") else None
        mid = tags.get("msg-id", "")
        if mid in ("sub", "resub"):
            return {"k": "sub", "n": cls._num(tags.get("msg-param-cumulative-months", ""), 1200)}
        if mid == "subgift":
            return False if tags.get("msg-param-community-gift-id") else {"k": "gift", "n": 1}
        if mid == "submysterygift":
            return {"k": "gift", "n": cls._num(tags.get("msg-param-mass-gift-count", ""), 1000) or 1}
        if mid in ("giftpaidupgrade", "anongiftpaidupgrade"):
            return {"k": "sub", "n": 0}
        if mid == "raid":
            return {"k": "raid", "n": cls._num(tags.get("msg-param-viewerCount", ""), 10_000_000)}
        if mid == "announcement":
            return {"k": "announce", "n": 0}
        return None

    def _add(self, item, gen=None):
        with self.lock:
            if gen is not None and gen != self.gen:
                return
            item["id"], item["t"] = self.next_id, int(item.get("ts") or self.wall())
            self.next_id += 1
            self.items.append(item)

    def _user_ready(self, uid):
        patched = []
        with self.lock:
            for it in self.items:
                if it.get("uid") == uid and it.get("mid") and it.get("type") in ("msg", "me") and self.third.mark_user(it):
                    patched.append((it["mid"], [list(e) for e in it["emotes"]]))
        for mid, em in patched:
            self._add({"type": "emotes", "meta": True, "mid": mid, "text": "", "emotes": em})

    def poll(self, since=0):
        ch = ""
        if self.store:
            ch = self.store.channel_name() if hasattr(self.store, "channel_name") else self.store.data.get("channel", "")
        now = self.clock()
        with self.lock:
            self.last_poll = now
        if self.demo:
            self._demo(now)
            ch = ch or "demo"
            state = "ok"
        elif not ch:
            return {"state": "kein-kanal", "channel": "", "items": [], "last": 0}
        else:
            self._ensure(ch)
            state = self.state
            if self.events is not None:
                self.events.ensure()
        with self.lock:
            last = self.next_id - 1
            if since > last:
                since = 0
            items = [i for i in self.items if i["id"] > since]
        return {"state": state, "channel": ch, "items": items, "last": last}

    def _ensure(self, ch):
        with self.lock:
            alive = self.thread is not None and self.thread.is_alive() and self.channel == ch
            if alive:
                return
            if self.channel != ch:
                self.items.clear()
            self.channel = ch
            self.state = "verbinde"
            self.gen += 1
            gen = self.gen
            t = threading.Thread(target=self._run, args=(ch, gen), daemon=True)
            self.thread = t
        t.start()

    def _idle(self):
        return self.clock() - self.last_poll > self.IDLE

    def _mine(self, ch, gen):
        return self.gen == gen and self.channel == ch

    def _run(self, ch, gen):
        wait = 3.0
        while not self._idle() and self._mine(ch, gen):
            began = self.clock()
            try:
                self._session(ch, gen)
            except Exception:
                with self.lock:
                    if self._mine(ch, gen):
                        self.state = "fehler"
            if self.clock() - began > 30:
                wait = 3.0
            end = self.clock() + wait
            while self.clock() < end and not self._idle() and self._mine(ch, gen):
                self.sleep(0.5)
            wait = min(wait * 2, 60.0)
        with self.lock:
            if self._mine(ch, gen):
                self.state = "aus"

    def _session(self, ch, gen):
        sock, src = self.paths.connect(self.host, self.port, timeout=8)
        ok = False
        try:
            if self.tls:
                sock = (self.context or ssl.create_default_context()).wrap_socket(sock, server_hostname=self.host)
            nick = "justinfan%05d" % random.randint(0, 99999)
            sock.sendall(("PASS SCHMOOPIIE\r\nNICK %s\r\n" % nick).encode("ascii"))
            rd = _IrcLines(sock, self.clock)
            until = self.clock() + 15
            while True:
                line = rd.get(until)
                if line is None:
                    raise OSError
                cmd = line.partition(" ")[2].partition(" ")[0] if line.startswith(":") else line.partition(" ")[0]
                if cmd == "001":
                    break
                if cmd == "PING":
                    sock.sendall(b"PONG :tmi.twitch.tv\r\n")
            sock.sendall(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\nJOIN #" + ch.encode("ascii") + b"\r\n")
            ok = True
            with self.lock:
                if self._mine(ch, gen):
                    self.state = "ok"
            heard = probed = self.clock()
            while not self._idle() and self._mine(ch, gen):
                line = rd.get(self.clock() + 5)
                if line is None:
                    if self.clock() - heard > self.PROBE and self.clock() - probed > self.PROBE:
                        sock.sendall(b"PING :pipbox\r\n")
                        probed = self.clock()
                    if self.clock() - heard > self.STALE:
                        self.paths.fail(src, 300.0)
                        raise OSError("still")
                    continue
                heard = self.clock()
                if line.startswith("PING"):
                    sock.sendall(b"PONG :tmi.twitch.tv\r\n")
                    continue
                if TwitchChat._parse(line)[0] == "RECONNECT":
                    return
                try:
                    item = self.parse(line)
                    if item:
                        self._add(item, gen)
                except Exception:
                    continue
        except OSError:
            if not ok:
                self.paths.fail(src, 120.0)
            raise
        finally:
            try:
                sock.close()
            except OSError:
                pass

    DEMO = [("Anna_Streams", "#FF4500", "Hallo aus dem Chat! Das Bild ist heute richtig scharf"), ("kamerafreak", "#1E90FF", "Welche Kamera ist das gerade?"),
            ("Moderator_Max", "#2E8B57", "Bitte keine Werbung im Chat"), ("nightowl", "", "gg 🎉"), ("Lena", "#DAA520", "Der Ton ist super")]

    def _demo(self, now):
        if now - self.demo_at < 2.0 and self.items:
            return
        self.demo_at = now
        n = self.next_id
        if n % 11 == 0:
            self._add({"type": "sub", "name": "", "color": "", "text": "Anna_Streams hat den Kanal abonniert (Stufe 1)", "emotes": [], "ev": {"k": "sub", "n": 0}})
        elif n % 13 == 0:
            self._add({"type": "raid", "name": "", "color": "", "text": "Max_Raider schickt 42 Zuschauer zu dir", "emotes": [], "ev": {"k": "raid", "n": 42}})
        elif n % 17 == 0:
            self._add({"type": "msg", "name": "Lena", "color": "#DAA520", "text": "cheer500 Weiter so!", "emotes": [], "bits": 500, "ev": {"k": "cheer", "n": 500},
                       "mid": "%08x-0000-4000-8000-000000000000" % n, "uid": "102", "login": "lena", "badges": ["vip", "subscriber"]})
        elif n % 19 == 0:
            self._add({"type": "sub", "name": "", "color": "", "text": "nightowl verschenkt 5 Abos an die Community", "emotes": [], "ev": {"k": "gift", "n": 5}})
        elif n % 23 == 0:
            self._add({"type": "notice", "name": "", "color": "", "text": "Anna_Streams", "emotes": [], "ev": {"k": "follow", "n": 0}})
        elif n % 29 == 0:
            self._add({"type": "notice", "name": "", "color": "", "text": "Lena: Trink etwas Wasser", "emotes": [], "ev": {"k": "points", "n": 500}})
        elif n % 7 == 0 and (self.third.ensure("") or True) and self.third.snapshot():
            names = sorted(self.third.snapshot())
            a, b = names[n % len(names)], names[(n * 7) % len(names)]
            item = {"type": "msg", "name": "emote_fan", "color": "#8A2BE2", "text": "Der Chat zeigt jetzt %s und %s" % (a, b), "emotes": [],
                    "mid": "%08x-0000-4000-8000-000000000000" % n, "uid": "103", "login": "emote_fan", "badges": []}
            self.third.mark(item)
            self._add(item)
        else:
            i = n % len(self.DEMO)
            name, color, text = self.DEMO[i]
            badges = {"Moderator_Max": ["moderator"], "Anna_Streams": ["subscriber"], "Lena": ["vip", "subscriber"]}.get(name, [])
            self._add({"type": "msg", "name": name, "color": color, "text": text, "emotes": [], "mid": "%08x-0000-4000-8000-000000000000" % n,
                       "uid": str(100 + i), "login": name.lower(), "badges": badges})
