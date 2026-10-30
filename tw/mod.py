
import json
import threading
import time

from tw.irc import ChatPaths, TwitchChat, _ascii_digits
from tw.login import TwitchLogin
from tw.reader import TwitchReader
from tw.store import TwitchStore


class TwitchMod:
    API = "https://api.twitch.tv/helix/"
    MAX_SECONDS = 1209600
    ERR_NET = "Keine Verbindung zu Twitch"
    ERR_LOGIN = "Zuerst mit Twitch anmelden"
    ERR_SCOPE = "Moderation ist noch nicht eingeschaltet"
    ERR_AUTH = "Anmeldung abgelaufen, bitte neu anmelden"
    ERR_FORBIDDEN = "Dazu fehlt die Berechtigung (Moderator im Kanal?)"
    ERR_OTHER = "Twitch hat die Anfrage nicht angenommen"
    ERR_UNSURE = "Verbindung gestört, unklar ob es angekommen ist"
    ERR_RIGHTS = "Dafür fehlt ein Recht: bitte „Moderation einschalten“ und bei Twitch bestätigen"
    ERR_OWNER = "Das darf nur der Kanalinhaber (im eigenen Kanal)"
    ERR_BROWSER = "Das geht nur in dem Browser, in dem sich der Streamer bei Twitch angemeldet hat (dort einmal neu anmelden)"
    ERR_NOTFOUND = "Twitch findet das nicht (läuft der Stream?)"
    CMD_SCOPE = {"vip": "channel:manage:vips", "unvip": "channel:manage:vips", "mod": "channel:manage:moderators", "unmod": "channel:manage:moderators",
                 "clear": "moderator:manage:chat_messages", "slow": "moderator:manage:chat_settings", "slowoff": "moderator:manage:chat_settings",
                 "followers": "moderator:manage:chat_settings", "followersoff": "moderator:manage:chat_settings",
                 "subscribers": "moderator:manage:chat_settings", "subscribersoff": "moderator:manage:chat_settings",
                 "emoteonly": "moderator:manage:chat_settings", "emoteonlyoff": "moderator:manage:chat_settings",
                 "announce": "moderator:manage:announcements", "raid": "channel:manage:raids", "unraid": "channel:manage:raids",
                 "marker": "channel:manage:broadcast", "title": "channel:manage:broadcast", "game": "channel:manage:broadcast"}
    OWNER_ONLY = ("vip", "unvip", "mod", "unmod", "raid", "unraid", "marker", "title", "game")
    SETTINGS = {"slowoff": ("slow_mode", "Langsamer Modus aus"), "followersoff": ("follower_mode", "Nur Follower aus"),
                "subscribersoff": ("subscriber_mode", "Nur Abonnenten aus"), "emoteonlyoff": ("emote_mode", "Nur Emotes aus"),
                "subscribers": ("subscriber_mode", "Nur Abonnenten an"), "emoteonly": ("emote_mode", "Nur Emotes an")}

    def __init__(self, store, account, api_base=None, client_id=None, demo_reader=None, demo=False, paths=None):
        self.store, self.account = store, account
        self.paths = paths or getattr(account, "paths", None) or ChatPaths()
        self.api = api_base or self.API
        self.client_id = client_id or TwitchLogin.CLIENT_ID
        self.demo, self.demo_reader = demo, demo_reader
        self.ids = {}
        self.lock = threading.Lock()

    def _call(self, method, path, params=None, body=None, once=False, timeout=15.0, token=None, prefer=False, accept=()):
        import urllib.error
        import urllib.parse
        import urllib.request
        tok = token or self.account.token()
        if not tok:
            raise ValueError(self.ERR_LOGIN)
        url = self.api + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        hdr = {"Authorization": "Bearer " + tok, "Client-Id": self.client_id, "User-Agent": "irl4you-box"}
        if data is not None:
            hdr["Content-Type"] = "application/json"
        status, raw = self.paths.request(method, url, data, hdr, timeout=timeout, once=once, prefer=prefer)
        if status == 401 and token is None and self.account.refresh_now():
            hdr["Authorization"] = "Bearer " + self.account.token()
            status, raw = self.paths.request(method, url, data, hdr, timeout=timeout, once=once, prefer=prefer)
        if status == 0:
            raise ValueError(self.ERR_NET)
        if status == -1:
            raise ValueError(self.ERR_UNSURE)
        try:
            out = json.loads(raw.decode("utf-8", "replace") or "{}")
        except ValueError:
            out = {}
        out = out if isinstance(out, dict) else {}
        if status in (200, 201, 202, 204) or status in accept:
            return out
        if status == 401:
            raise ValueError(self.ERR_AUTH)
        if status == 403:
            raise ValueError(self.ERR_FORBIDDEN)
        if status == 404:
            raise ValueError(self.ERR_NOTFOUND)
        msg = TwitchReader._clean(str(out.get("message", "")), 120)
        raise ValueError(msg if status in (400, 409, 422, 425) and msg else self.ERR_OTHER)

    def is_moderator(self, uid):
        if self.demo:
            return True
        try:
            bc, me = self._context()
            if bc != me or not _ascii_digits(str(uid)):
                return None
            if "channel:manage:moderators" not in (self.account.status().get("scopes") or []):
                return None
            rows = self._call("GET", "moderation/moderators", {"broadcaster_id": bc, "user_id": str(uid)}).get("data")
        except ValueError:
            return None
        return bool(isinstance(rows, list) and rows)

    def view(self, account):
        import copy
        v = copy.copy(self)
        v.account = account
        return v

    def _context(self):
        if not self.account.ready():
            raise ValueError(self.ERR_LOGIN)
        if not self.account.status().get("mod"):
            raise ValueError(self.ERR_SCOPE)
        me, mylogin = self.account.user_id(), self.account.login()
        ch = (self.store.channel_name() or mylogin).lower()
        return (me if ch == mylogin else self.lookup(ch)), me

    def lookup(self, login):
        login = str(login).strip().lstrip("@").lower()
        if not TwitchStore.NAME_RE.fullmatch(login):
            raise ValueError("Ungültiger Name")
        with self.lock:
            if login in self.ids:
                return self.ids[login]
        d = self._call("GET", "users", {"login": login})
        rows = d.get("data") if isinstance(d.get("data"), list) else []
        if not rows or not isinstance(rows[0], dict) or not str(rows[0].get("id", "")).isdigit():
            raise ValueError("Dieses Konto gibt es nicht")
        with self.lock:
            if len(self.ids) > 200:
                self.ids.clear()
            self.ids[login] = str(rows[0]["id"])
            return self.ids[login]

    def do(self, req, owner_key=None):
        if not isinstance(req, dict):
            raise ValueError("Ungültige Anfrage")
        act = req.get("action")
        if act in self.CMD_SCOPE:
            return self._extra(act, req, owner_key)
        if act not in ("delete", "timeout", "ban", "unban"):
            raise ValueError("Ungültige Anfrage")
        if self.demo:
            return self._demo(act, req)
        bc, me = self._context()
        base = {"broadcaster_id": bc, "moderator_id": me}
        if act == "delete":
            mid = req.get("message_id")
            if not isinstance(mid, str) or not TwitchReader.MID_RE.fullmatch(mid):
                raise ValueError("Ungültige Anfrage")
            self._call("DELETE", "moderation/chat", dict(base, message_id=mid))
            return {"ok": True, "action": act, "message": "Gelöscht"}
        uid = self._target(req)
        if act == "unban":
            self._call("DELETE", "moderation/bans", dict(base, user_id=uid))
            return {"ok": True, "action": act, "user_id": uid, "message": "Bann aufgehoben"}
        data = {"user_id": uid}
        reason = TwitchReader._clean(str(req.get("reason") or ""), 200)
        if reason:
            data["reason"] = reason
        if act == "timeout":
            try:
                sec = int(req.get("seconds", 600))
            except (TypeError, ValueError, OverflowError):
                raise ValueError("Dauer: Zahl in Sekunden")
            data["duration"] = max(1, min(self.MAX_SECONDS, sec))
        self._call("POST", "moderation/bans", base, {"data": data})
        return {"ok": True, "action": act, "user_id": uid, "message": "Timeout" if act == "timeout" else "Gebannt"}

    def _extra(self, act, req, owner_key=None):
        if self.demo:
            return self._demo(act, req)
        bc, me = self._context()
        if act in self.OWNER_ONLY and bc != me:
            raise ValueError(self.ERR_OWNER)
        if self.CMD_SCOPE[act] not in (self.account.status().get("scopes") or []):
            raise ValueError(self.ERR_RIGHTS)
        if act in self.OWNER_ONLY and not self.account.is_owner(owner_key):
            raise ValueError(self.ERR_BROWSER)
        base = {"broadcaster_id": bc, "moderator_id": me}
        text = TwitchReader._clean(str(req.get("text") or ""), 500).strip()
        if act in ("vip", "unvip", "mod", "unmod"):
            path = "channels/vips" if act in ("vip", "unvip") else "moderation/moderators"
            self._call("POST" if act in ("vip", "mod") else "DELETE", path, {"broadcaster_id": bc, "user_id": self._target(req)})
            msg = {"vip": "VIP vergeben", "unvip": "VIP entfernt", "mod": "Moderator ernannt", "unmod": "Moderator entfernt"}[act]
        elif act == "clear":
            self._call("DELETE", "moderation/chat", base)
            msg = "Chat geleert"
        elif act in ("slow", "followers"):
            try:
                n = int(req.get("number")) if req.get("number") is not None else None
            except (TypeError, ValueError, OverflowError):
                raise ValueError("Die Zahl ist ungültig")
            if act == "slow":
                n = 30 if n is None else max(3, min(120, n))
                body, msg = {"slow_mode": True, "slow_mode_wait_time": n}, "Langsamer Modus an (%d Sekunden)" % n
            else:
                n = 0 if n is None else max(0, min(129600, n))
                body, msg = {"follower_mode": True, "follower_mode_duration": n}, ("Nur Follower an" if n == 0 else "Nur Follower an (seit %d Minuten)" % n)
            self._call("PATCH", "chat/settings", base, body)
        elif act in self.SETTINGS:
            key, msg = self.SETTINGS[act]
            self._call("PATCH", "chat/settings", base, {key: not act.endswith("off")})
        elif act == "announce":
            if not text:
                raise ValueError("Der Text fehlt")
            self._call("POST", "chat/announcements", base, {"message": text, "color": "primary"})
            msg = "Ankündigung gesendet"
        elif act == "raid":
            self._call("POST", "raids", {"from_broadcaster_id": bc, "to_broadcaster_id": self._target(req)})
            msg = "Raid gestartet"
        elif act == "unraid":
            self._call("DELETE", "raids", {"broadcaster_id": bc})
            msg = "Raid abgebrochen"
        elif act == "marker":
            self._call("POST", "streams/markers", None, {"user_id": bc, "description": text[:140]})
            msg = "Marker gesetzt"
        elif act == "title":
            if not text:
                raise ValueError("Der Text fehlt")
            self._call("PATCH", "channels", {"broadcaster_id": bc}, {"title": text[:140]})
            msg = "Titel geändert"
        else:
            if not text:
                raise ValueError("Der Name der Kategorie fehlt")
            rows = self._call("GET", "games", {"name": text[:140]}).get("data")
            row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
            if not str(row.get("id", "")).isdigit():
                raise ValueError("Diese Kategorie gibt es nicht")
            self._call("PATCH", "channels", {"broadcaster_id": bc}, {"game_id": str(row["id"])})
            msg = "Kategorie geändert"
        return {"ok": True, "action": act, "message": msg}

    def _target(self, req):
        uid = req.get("user_id")
        if isinstance(uid, str) and _ascii_digits(uid) and len(uid) <= 20:
            return uid
        if isinstance(req.get("user"), str):
            return self.lookup(req["user"])
        raise ValueError("Ungültige Anfrage")

    def _demo(self, act, req):
        if act in self.CMD_SCOPE:
            return {"ok": True, "action": act, "message": "Demo: Befehl ausgeführt"}
        if act == "delete" and self.demo_reader is not None and isinstance(req.get("message_id"), str):
            self.demo_reader._add({"type": "del", "meta": True, "mid": req["message_id"], "text": "", "emotes": []})
        if act in ("timeout", "ban") and self.demo_reader is not None and _ascii_digits(str(req.get("user_id") or "")):
            self.demo_reader._add({"type": "clear", "meta": True, "uid": str(req["user_id"]), "seconds": int(req.get("seconds") or 0) if act == "timeout" else 0,
                                   "text": "", "emotes": []})
        return {"ok": True, "action": act, "user_id": str(req.get("user_id") or "1"),
                "message": {"delete": "Gelöscht", "timeout": "Timeout", "ban": "Gebannt", "unban": "Bann aufgehoben"}[act]}


class TwitchSender:
    COMMANDS = ("ban", "timeout", "unban")
    NAMED = ("vip", "unvip", "mod", "unmod", "raid")
    PLAIN = ("clear", "slowoff", "followersoff", "subscribers", "subscribersoff", "emoteonly", "emoteonlyoff", "unraid")
    NUMBER = ("slow", "followers")
    TEXT = ("announce", "title", "game")
    HELP = ("Befehle: /ban, /timeout, /unban, /vip, /unvip, /mod, /unmod, /clear, /slow, /slowoff, /followers, /followersoff, /subscribers, /subscribersoff, "
            "/emoteonly, /emoteonlyoff, /announce, /raid, /unraid, /marker, /title, /game")
    MIN_GAP = 1.0

    def __init__(self, store, chat=None, clock=time.monotonic, demo_reader=None, mod=None, helix=None):
        self.store, self.chat, self.clock, self.mod, self.helix = store, chat or TwitchChat(), clock, mod, helix
        self.demo_reader = demo_reader
        self.last = -1e9
        self.lock = threading.Lock()

    def _command(self, t, owner_key=None, mod=None):
        mod = mod or self.mod
        parts = t[1:].split()
        cmd = parts[0].lower() if t[0] == "/" and parts else ""
        known = self.COMMANDS + self.NAMED + self.PLAIN + self.NUMBER + self.TEXT + ("marker",)
        if cmd not in known or mod is None:
            raise ValueError(self.HELP)
        if cmd in self.COMMANDS + self.NAMED + self.TEXT and len(parts) < 2:
            raise ValueError(self.HELP)
        with self.lock:
            now = self.clock()
            if now - self.last < self.MIN_GAP:
                raise ValueError("Bitte kurz warten")
            self.last = now
        if cmd in self.COMMANDS:
            req = {"action": cmd, "user": parts[1]}
            rest = parts[2:]
            if cmd == "timeout" and rest and _ascii_digits(rest[0]):
                req["seconds"] = int(rest[0])
                rest = rest[1:]
            if cmd != "unban" and rest:
                req["reason"] = " ".join(rest)
            out = mod.do(req, owner_key)
            return {"ok": True, "message": "%s: %s" % (out.get("message", ""), parts[1].lstrip("@"))}
        req = {"action": cmd}
        if cmd in self.NAMED:
            req["user"] = parts[1]
        elif cmd in self.NUMBER and len(parts) > 1:
            if not _ascii_digits(parts[1]):
                raise ValueError("Die Zahl ist ungültig")
            req["number"] = int(parts[1])
        elif cmd in self.TEXT or cmd == "marker":
            req["text"] = " ".join(parts[1:])
        out = mod.do(req, owner_key)
        who = ": " + parts[1].lstrip("@") if cmd in self.NAMED else ""
        return {"ok": True, "message": out.get("message", "") + who}

    def say_as(self, mod, helix, text, owner_key=None, commands=True):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Nachricht fehlt")
        t = text.strip()
        if t[0] in "/.":
            if not commands:
                raise ValueError("Befehle gibt es nur für den Streamer und Moderatoren")
            return self._command(t, owner_key, mod)
        if not helix.account.ready():
            raise ValueError("Zuerst mit Twitch anmelden")
        with self.lock:
            now = self.clock()
            if now - self.last < self.MIN_GAP:
                raise ValueError("Bitte kurz warten")
            self.last = now
        if self.demo_reader is not None:
            self.demo_reader._add({"type": "msg", "name": helix.account.login(), "color": "#9146FF", "text": TwitchChat.clean_text(t) or "", "emotes": []})
            return {"ok": True, "message": TwitchChat.OK}
        t = TwitchChat.clean_text(t)
        if not t:
            raise ValueError("Nachricht ungültig")
        if not helix.usable():
            raise ValueError("Für das Schreiben fehlt ein Recht: bitte neu anmelden")
        return {"ok": True, "message": helix.send(self.store.channel_name(), t)}

    def say(self, text, owner_key=None):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Nachricht fehlt")
        t = text.strip()
        if t[0] in "/.":
            return self._command(t, owner_key)
        cfg = self.store.settings()
        if not (cfg["login"] and cfg["token"] and cfg["channel"]):
            raise ValueError("Zuerst mit Twitch anmelden")
        with self.lock:
            now = self.clock()
            if now - self.last < self.MIN_GAP:
                raise ValueError("Bitte kurz warten")
            self.last = now
        if self.demo_reader is not None:
            self.demo_reader._add({"type": "msg", "name": cfg["login"], "color": "#9146FF", "text": TwitchChat.clean_text(t) or "", "emotes": []})
            return {"ok": True, "message": TwitchChat.OK}
        t = TwitchChat.clean_text(t)
        if not t:
            raise ValueError("Nachricht ungültig")
        if self.helix is not None and self.helix.usable():
            return {"ok": True, "message": self.helix.send(cfg["channel"], t)}
        ok, msg = self.chat.send(cfg["login"], cfg["token"], cfg["channel"], t)
        if not ok:
            raise ValueError(msg)
        return {"ok": True, "message": msg}


class HelixChat:
    ERR_DROP = "Twitch hat die Nachricht nicht gesendet"
    ERR_FORBIDDEN = "Twitch erlaubt das Senden hier gerade nicht (zum Beispiel nur für Abonnenten oder Follower)"

    def __init__(self, account, mod):
        self.account, self.mod = account, mod

    def usable(self):
        try:
            return bool(self.account.ready() and "user:write:chat" in (self.account.status().get("scopes") or []))
        except Exception:
            return False

    def send(self, channel, text):
        me = self.account.user_id()
        if not me:
            raise ValueError("Zuerst mit Twitch anmelden")
        ch = str(channel or "").strip().lstrip("#").lower()
        bid = me if (not ch or ch == self.account.login()) else self.mod.lookup(ch)
        try:
            d = self.mod._call("POST", "chat/messages", None, {"broadcaster_id": bid, "sender_id": me, "message": text}, once=True)
        except ValueError as e:
            if str(e) == self.mod.ERR_FORBIDDEN:
                raise ValueError(self.ERR_FORBIDDEN)
            raise
        rows = d.get("data") if isinstance(d.get("data"), list) else []
        row = rows[0] if rows and isinstance(rows[0], dict) else {}
        if row.get("is_sent") is True:
            return TwitchChat.OK
        why = row.get("drop_reason") if isinstance(row.get("drop_reason"), dict) else {}
        msg = TwitchReader._clean(str(why.get("message") or ""), 120)
        raise ValueError(self.ERR_DROP + (": " + msg if msg else ""))
