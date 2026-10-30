
import json
import math
import os
import re
import threading

from tw.util import KEY_RE


class TwitchStore:
    NAME_RE = re.compile(r"[A-Za-z0-9_]{3,25}")
    TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,100}")
    DEFAULT_MESSAGE = "Akkustand niedrig, bitte Akku wechseln: {Kamera} ({Prozent} %)"
    DEFAULTS = {"enabled": False, "login": "", "token": "", "channel": "", "threshold": 10, "message": DEFAULT_MESSAGE, "only_live": True, "via_account": False}

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = dict(self.DEFAULTS)
        self.warned = {}
        self.account = None
        self.bot = None
        try:
            with open(path) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = None
        if isinstance(saved, dict):
            self._adopt(saved)

    @classmethod
    def _name(cls, v, error):
        if not isinstance(v, str):
            raise ValueError(error)
        v = v.strip()
        if v and not cls.NAME_RE.fullmatch(v):
            raise ValueError(error)
        return v.lower()

    @classmethod
    def _token(cls, v):
        if not isinstance(v, str):
            raise ValueError("Token: 20 bis 100 Zeichen, nur Buchstaben, Ziffern, _ und -")
        v = v.strip()
        if not v:
            return ""
        if v[:6].lower() == "oauth:":
            v = v[6:]
        if not cls.TOKEN_RE.fullmatch(v):
            raise ValueError("Token: 20 bis 100 Zeichen, nur Buchstaben, Ziffern, _ und -")
        return v

    @staticmethod
    def _threshold(v):
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 50:
            raise ValueError("Warnen bei: Zahl von 1 bis 50")
        return v

    @staticmethod
    def _message(v):
        if not isinstance(v, str) or not 1 <= len(v.strip()) <= 300:
            raise ValueError("Nachricht: 1 bis 300 Zeichen")
        v = v.strip()
        if any(not ch.isprintable() for ch in v):
            raise ValueError("Nachricht: keine Sonderzeichen oder Zeilenumbrüche")
        if v[0] in "/.":
            raise ValueError("Nachricht: darf nicht mit / oder . beginnen")
        return v

    ERR_CHANNEL = "Kanal: 3 bis 25 Zeichen, nur Buchstaben, Ziffern und _"
    ERR_LOGIN = "Bot-Konto: 3 bis 25 Zeichen, nur Buchstaben, Ziffern und _"
    ERR_MISSING = "Bitte Kanal, Bot-Konto und Token eintragen"

    def _adopt(self, saved):
        for key, check in (("login", lambda v: self._name(v, self.ERR_LOGIN)), ("channel", lambda v: self._name(v, self.ERR_CHANNEL)), ("token", self._token),
                           ("threshold", self._threshold), ("message", self._message)):
            if key in saved:
                try:
                    self.data[key] = check(saved[key])
                except ValueError:
                    pass
        if isinstance(saved.get("only_live"), bool):
            self.data["only_live"] = saved["only_live"]
        d = self.data
        d["via_account"] = saved.get("via_account") is True
        d["enabled"] = saved.get("enabled") is True and (bool(d["channel"] and d["login"] and d["token"]) or d["via_account"])
        w = saved.get("warned")
        if isinstance(w, dict):
            self.warned = {k: float(t) for k, t in w.items() if isinstance(k, str) and KEY_RE.match(k) and isinstance(t, (int, float)) and not isinstance(t, bool)
                           and math.isfinite(t) and t > 0}

    def _write(self, data, warned):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump(dict(data, warned=warned), f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def set(self, req):
        if not isinstance(req, dict):
            raise ValueError("Ungültige Anfrage")
        with self.lock:
            new = dict(self.data)
            for key in ("enabled", "only_live"):
                if key in req:
                    if not isinstance(req[key], bool):
                        raise ValueError("ja oder nein")
                    new[key] = req[key]
            if "channel" in req:
                new["channel"] = self._name(req["channel"], self.ERR_CHANNEL)
            if "login" in req:
                new["login"] = self._name(req["login"], self.ERR_LOGIN)
            if "token" in req:
                new["token"] = self._token(req["token"]) or new["token"]
            if "threshold" in req:
                new["threshold"] = self._threshold(req["threshold"])
            if "message" in req:
                new["message"] = self._message(req["message"])
            acc = self.account.login() if self.account else ""
            bot = self.bot.login() if self.bot else ""
            manual = bool(new["channel"] and new["login"] and new["token"])
            if new["enabled"] and not (manual or acc or (bot and new["channel"])):
                raise ValueError(self.ERR_MISSING)
            new["via_account"] = bool(new["enabled"] and not manual)
            try:
                self._write(new, self.warned)
            except OSError:
                raise RuntimeError("Die Einstellungen konnten nicht gespeichert werden")
            self.data = new

    def public(self):
        with self.lock:
            d = self.data
            acc = self.account.login() if self.account else ""
            bot = self.bot.login() if self.bot else ""
            return {"enabled": d["enabled"], "channel": d["channel"] or acc, "login": d["login"], "token_set": bool(d["token"]), "threshold": d["threshold"],
                    "message": d["message"], "only_live": d["only_live"], "account": acc, "bot": bot}

    def settings(self):
        with self.lock:
            d = dict(self.data)
        acc = self.account.login() if self.account else ""
        if acc:
            tok = self.account.token()
            d["login"], d["token"] = acc, tok
            d["channel"] = d["channel"] or acc
        return d

    def channel_name(self):
        with self.lock:
            ch = self.data["channel"]
        return ch or (self.account.login() if self.account else "")

    def notify_settings(self):
        d = self.settings()
        bot = self.bot.login() if self.bot else ""
        if bot:
            d["login"], d["token"] = bot, self.bot.token()
        elif self.bot and self.bot.state == "abgelaufen":
            d["token"] = ""
        return d

    def warned_keys(self):
        with self.lock:
            return list(self.warned)

    def is_warned(self, key):
        with self.lock:
            return key in self.warned

    def _flush(self):
        try:
            self._write(self.data, self.warned)
        except OSError:
            pass

    def mark_warned(self, key, when):
        with self.lock:
            self.warned[key] = float(when)
            self._flush()

    def unwarn(self, key):
        with self.lock:
            if self.warned.pop(key, None) is not None:
                self._flush()
