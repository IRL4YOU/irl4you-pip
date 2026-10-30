
import hashlib
import json
import os
import re
import threading
import time

from tw.irc import ChatPaths


class TwitchLogin:
    CLIENT_ID = "a9w1hzb4d6nu70re8wxt6rpyxths35"
    ID_BASE = "https://id.twitch.tv/oauth2/"
    SCOPES = "chat:read chat:edit user:write:chat"
    CMD_SCOPES = ("channel:manage:vips channel:manage:moderators moderator:manage:chat_settings moderator:manage:announcements "
                  "channel:manage:raids channel:manage:broadcast")
    MOD_BASE = "moderator:manage:banned_users moderator:manage:chat_messages"
    MOD_SCOPES = MOD_BASE + " " + CMD_SCOPES
    EVENT_SCOPES = "moderator:read:followers channel:read:redemptions"
    SCOPES_MOD = "chat:read chat:edit user:write:chat " + MOD_SCOPES
    GRANT = "urn:ietf:params:oauth:grant-type:device_code"
    OWNER_RE = re.compile(r"[0-9a-f]{64}")
    KEY_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")
    MAX_OWNERS = 5
    DEMO_LOGIN = "demo_streamer"
    ERR_NOT_OWNER = "Das ist nicht das Konto des Streamers dieser Box"
    ERR_NO_CHANNEL = "Bitte zuerst eintragen, auf welchem Konto gestreamt wird (Kanal)"
    REFRESH_BEFORE = 600
    KEEP_EVERY = 30.0
    ERR_NET = "Keine Verbindung zu Twitch"
    ERR_DENIED = "Die Anmeldung wurde abgelehnt oder ist abgelaufen"

    def __init__(self, path, client_id=None, id_base=None, clock=time.time, sleep=time.sleep, demo=False, paths=None):
        self.paths = paths or ChatPaths()
        self.path, self.client_id = path, client_id or self.CLIENT_ID
        self.base = id_base or self.ID_BASE
        self.clock, self.sleep, self.demo = clock, sleep, demo
        self.lock = threading.RLock()
        self.tokens = None
        self.pending = None
        self.state, self.error = "aus", ""
        self.gen = 0
        self.claim_fail = None
        try:
            with open(path) as f:
                saved = json.load(f)
            if isinstance(saved, dict) and isinstance(saved.get("access"), str) and isinstance(saved.get("refresh"), str):
                self.tokens = {"access": saved["access"], "refresh": saved["refresh"], "expires_at": float(saved.get("expires_at", 0)),
                               "login": str(saved.get("login", ""))[:25], "user_id": str(saved.get("user_id", ""))[:20],
                               "scopes": [str(x)[:60] for x in saved.get("scopes", [])][:30], "mod_off": saved.get("mod_off") is True,
                               "owners": [x for x in saved.get("owners", []) if isinstance(x, str) and self.OWNER_RE.fullmatch(x)][:self.MAX_OWNERS]}
                self.state = "angemeldet"
        except (OSError, ValueError, TypeError):
            pass

    def _call(self, url, fields=None, headers=None, once=False):
        import urllib.parse
        data = urllib.parse.urlencode(fields).encode() if fields is not None else None
        hdr = dict(headers or {}, **{"User-Agent": "irl4you-box"})
        if data is not None:
            hdr["Content-Type"] = "application/x-www-form-urlencoded"
        status, body = self.paths.request("POST" if data is not None else "GET", url, data, hdr, once=once)
        try:
            out = json.loads(body.decode("utf-8", "replace") or "{}")
        except ValueError:
            out = {}
        return status, out if isinstance(out, dict) else {}

    def _write(self, tokens=None):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump(tokens if tokens is not None else self.tokens, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            try:
                dfd = os.open(os.path.dirname(os.path.abspath(self.path)), os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def status(self):
        if self.demo:
            with self.lock:
                self._demo_step()
        t, p, state = self.tokens, self.pending, self.state
        scopes = list((t or {}).get("scopes", []))
        has = bool(self.MOD_SCOPES) and all(x in scopes for x in self.MOD_SCOPES.split())
        ev = all(x in scopes for x in self.EVENT_SCOPES.split())
        out = {"state": state, "login": (t or {}).get("login", ""), "scopes": scopes, "error": self.error,
               "mod": has and not (t or {}).get("mod_off"),
               "mod_scope": has,
               "events": ev,
               "helix_chat": "user:write:chat" in scopes}
        if p and state == "wartet":
            out.update(code=p["user_code"], uri=p["uri"], expires_in=max(0, int(p["expires_at"] - self.clock())))
        return out

    def ready(self):
        return bool(self.tokens)

    def login(self):
        t = self.tokens
        return t.get("login", "") if t else ""

    def user_id(self):
        t = self.tokens
        return t.get("user_id", "") if t else ""

    def token(self):
        t = self.tokens
        if not t:
            return ""
        if t["expires_at"] - self.clock() < 60:
            with self.lock:
                if self.tokens and self.tokens["expires_at"] - self.clock() < 60:
                    self.refresh()
            t = self.tokens
        if t and t["expires_at"] - self.clock() > 0:
            return t["access"]
        return ""

    def scopes_for(self, mod=False, events=False, everything=False):
        want = self.SCOPES.split()
        have = set(((self.tokens or {}).get("scopes")) or [])
        if everything:
            mod = events = True
        for flag, group in ((mod, self.MOD_SCOPES), (events, self.EVENT_SCOPES)):
            for sc in group.split():
                if (flag or sc in have) and sc not in want:
                    want.append(sc)
        return " ".join(want)

    @classmethod
    def owner_hash(cls, key):
        if not isinstance(key, str) or not cls.KEY_RE.fullmatch(key):
            return None
        return hashlib.sha256(key.encode("ascii")).hexdigest()

    def is_owner(self, key):
        h = self.owner_hash(key)
        t = self.tokens
        return bool(h and t and h in (t.get("owners") or []))

    def start(self, mod=False, events=False, everything=False, owner_key=None, claim=False, expect=None):
        scopes = self.scopes_for(mod, events, everything)
        owner = self.owner_hash(owner_key)
        with self.lock:
            if self.claim_fail and self.claim_fail.get("owner") == owner:
                self.claim_fail = None
            if (self.state == "wartet" and self.pending and self.pending["expires_at"] > self.clock() and self.pending.get("scopes") == scopes
                    and self.pending.get("owner") == owner):
                return self.status()
            self.gen += 1
            gen = self.gen
            self.error = ""
            if self.demo:
                self.pending = {"device_code": "demo", "user_code": "ABCD-EFGH", "uri": "https://www.twitch.tv/activate?device-code=ABCDEFGH",
                                "expires_at": self.clock() + 1800, "interval": 5, "since": self.clock(), "scopes": scopes, "owner": owner, "claim": bool(claim),
                                "expect": str(expect or "").lower()[:25]}
                self.state = "wartet"
                return self.status()
            st, d = self._call(self.base + "device", {"client_id": self.client_id, "scopes": scopes})
            if st != 200 or not all(isinstance(d.get(k), str) for k in ("device_code", "user_code", "verification_uri")):
                self.state, self.error = ("angemeldet" if self.tokens else "fehler", self.ERR_NET if st == 0 else "Twitch hat die Anmeldung nicht angenommen")
                raise ValueError(self.error)
            try:
                interval = max(2, min(30, int(d.get("interval", 5))))
                expires = max(60, min(3600, int(d.get("expires_in", 1800))))
            except (TypeError, ValueError):
                interval, expires = 5, 1800
            uri = d["verification_uri"][:300]
            if not re.match(r"https://(www\.|id\.)?twitch\.tv/", uri):
                uri = "https://www.twitch.tv/activate"
            self.pending = {"device_code": d["device_code"], "user_code": d["user_code"][:20], "uri": uri,
                            "expires_at": self.clock() + expires, "interval": interval, "scopes": scopes, "owner": owner, "claim": bool(claim),
                            "expect": str(expect or "").lower()[:25]}
            self.state = "wartet"
        threading.Thread(target=self._poll, args=(gen,), daemon=True).start()
        return self.status()

    def cancel(self):
        with self.lock:
            self.gen += 1
            self.pending = None
            if self.state == "wartet":
                self.state = "angemeldet" if self.tokens else "aus"
            return self.status()

    def _poll(self, gen):
        while True:
            with self.lock:
                p = self.pending
                if gen != self.gen or not p or self.state != "wartet":
                    return
                if p["expires_at"] <= self.clock():
                    self.state, self.error, self.pending = ("angemeldet" if self.tokens else "fehler"), self.ERR_DENIED, None
                    return
                wait, device, scopes = p["interval"], p["device_code"], p.get("scopes", self.SCOPES)
            self.sleep(wait)
            st, d = self._call(self.base + "token", {"client_id": self.client_id, "scopes": scopes, "device_code": device, "grant_type": self.GRANT}, once=True)
            with self.lock:
                if gen != self.gen or self.state != "wartet":
                    if st == 200 and isinstance(d.get("access_token"), str):
                        self._call(self.base + "revoke", {"client_id": self.client_id, "token": d["access_token"]})
                    return
                if st == 200 and isinstance(d.get("access_token"), str) and isinstance(d.get("refresh_token"), str):
                    self._adopt(d)
                    return
                msg = str(d.get("message", "")).lower()
                if st == 400 and "authorization_pending" in msg:
                    continue
                if st == 400 and "slow_down" in msg:
                    self.pending["interval"] = min(30, self.pending["interval"] + 5)
                    continue
                if st <= 0 or st >= 500:
                    continue
                if st == 429:
                    self.pending["interval"] = min(30, self.pending["interval"] + 5)
                    continue
                self.state, self.error, self.pending = ("angemeldet" if self.tokens else "fehler"), self.ERR_DENIED, None
                return

    def _adopt(self, d):
        scopes = d.get("scope")
        scopes = [str(x)[:60] for x in scopes][:30] if isinstance(scopes, list) else self.SCOPES.split()
        try:
            expires = max(60.0, float(d.get("expires_in", 14400)))
        except (TypeError, ValueError):
            expires = 14400.0
        old = self.tokens or {}
        pend = (self.pending or {}).get("owner")
        want = (self.pending or {}).get("expect") or old.get("login")
        if (self.pending or {}).get("claim") and want:
            st0, v0 = self._call(self.base + "validate", None, {"Authorization": "OAuth " + d["access_token"]})
            who = v0.get("login", "").lower() if st0 == 200 and isinstance(v0.get("login"), str) else ""
            if who != want:
                self._call(self.base + "revoke", {"client_id": self.client_id, "token": d["access_token"]})
                self.claim_fail = {"owner": pend, "error": self.ERR_NOT_OWNER}
                self.pending, self.state, self.error = None, ("angemeldet" if self.tokens else "aus"), ""
                return
        t = {"access": d["access_token"], "refresh": d["refresh_token"], "expires_at": self.clock() + expires, "scopes": scopes,
             "login": old.get("login", ""), "user_id": old.get("user_id", ""), "mod_off": old.get("mod_off") is True,
             "owners": list(old.get("owners") or [])}
        werr = False
        try:
            self._write(t)
        except OSError:
            werr = True
        st, v = self._call(self.base + "validate", None, {"Authorization": "OAuth " + t["access"]})
        changed = False
        if st == 200 and isinstance(v.get("login"), str):
            new = (v["login"][:25].lower(), str(v.get("user_id", ""))[:20], [str(x)[:60] for x in v["scopes"]][:30] if isinstance(v.get("scopes"), list) else scopes)
            changed = new != (t["login"], t["user_id"], t["scopes"])
            if t["login"] and new[0] != t["login"]:
                t["owners"] = []
            t["login"], t["user_id"], t["scopes"] = new
        if pend and pend not in t["owners"]:
            t["owners"] = (t["owners"] + [pend])[-self.MAX_OWNERS:]
            changed = True
        self.tokens = t
        self.pending, self.state, self.error = None, "angemeldet", ""
        if changed:
            try:
                self._write()
                werr = False
            except OSError:
                werr = True
        if werr:
            self.error = "Die Anmeldung konnte nicht gespeichert werden"

    def refresh(self):
        with self.lock:
            if not self.tokens:
                return False
            if self.clock() < getattr(self, "next_try", 0.0):
                return False
            st, d = self._call(self.base + "token", {"client_id": self.client_id, "grant_type": "refresh_token", "refresh_token": self.tokens["refresh"]}, once=True)
            if st == 200 and isinstance(d.get("access_token"), str) and isinstance(d.get("refresh_token"), str):
                self.fails, self.next_try = 0, 0.0
                self._adopt(d)
                return True
            if st <= 0 or st == 429 or st >= 500:
                self.fails = getattr(self, "fails", 0) + 1
                self.next_try = self.clock() + min(300.0, 15.0 * 2 ** min(self.fails, 5))
                return False
            if st in (400, 401):
                keep = self.tokens.get("login", "")
                self.tokens, self.pending, self.state = None, None, "abgelaufen"
                self.error = keep
                try:
                    os.remove(self.path)
                except OSError:
                    pass
            return False

    def keep(self):
        while True:
            try:
                if self.tokens and self.tokens["expires_at"] - self.clock() < self.REFRESH_BEFORE:
                    with self.lock:
                        if self.tokens and self.tokens["expires_at"] - self.clock() < self.REFRESH_BEFORE:
                            self.refresh()
            except Exception:
                pass
            self.sleep(self.KEEP_EVERY)

    def set_mod(self, on):
        with self.lock:
            t = self.tokens
            if not t:
                raise ValueError("Zuerst mit Twitch anmelden")
            has = bool(self.MOD_SCOPES) and all(x in t.get("scopes", []) for x in self.MOD_SCOPES.split())
            if on and not has:
                raise ValueError("Moderation braucht eine neue Anmeldung bei Twitch")
            t["mod_off"] = not on
            try:
                self._write()
            except OSError:
                pass
            return self.status()

    def refresh_now(self):
        with self.lock:
            self.next_try = 0.0
            return bool(self.tokens) and self.refresh()

    def logout(self):
        with self.lock:
            self.gen += 1
            t = self.tokens
            self.tokens, self.pending, self.state, self.error = None, None, "aus", ""
            try:
                os.remove(self.path)
            except OSError:
                pass
        if t and not self.demo:
            self._call(self.base + "revoke", {"client_id": self.client_id, "token": t["access"]})
        return self.status()

    def _demo_step(self):
        if self.state == "wartet" and self.pending and self.clock() - self.pending.get("since", 0) > 6:
            self.tokens = {"access": "demo", "refresh": "demo", "expires_at": self.clock() + 14400, "login": self.DEMO_LOGIN, "user_id": "1",
                           "scopes": (self.pending.get("scopes") or self.SCOPES).split(), "owners": [self.pending["owner"]] if self.pending.get("owner") else []}
            self.pending, self.state = None, "angemeldet"


class TwitchBotLogin(TwitchLogin):
    SCOPES = "chat:read chat:edit"
    MOD_SCOPES = EVENT_SCOPES = ""


class TwitchModLogin(TwitchLogin):
    MOD_SCOPES = "moderator:manage:banned_users moderator:manage:chat_messages moderator:manage:chat_settings moderator:manage:announcements"
    EVENT_SCOPES = CMD_SCOPES = ""
    DEMO_LOGIN = "demo_moderator"


class ModSessions:
    MAX = 12

    def __init__(self, state_dir, demo=False, paths=None, id_base=None, client_id=None, clock=time.time, sleep=time.sleep):
        self.dir, self.demo, self.paths = state_dir, demo, paths
        self.kw = {"clock": clock, "sleep": sleep}
        if id_base:
            self.kw["id_base"] = id_base
        if client_id:
            self.kw["client_id"] = client_id
        self.items = {}
        self.lock = threading.Lock()

    def _path(self, h):
        return os.path.join(self.dir, "twitch-mod-%s.json" % h[:32])

    def _make(self, h):
        return TwitchModLogin(self._path(h), demo=self.demo, paths=self.paths, **self.kw)

    def _count(self):
        try:
            return len([n for n in os.listdir(self.dir) if n.startswith("twitch-mod-") and n.endswith(".json")])
        except OSError:
            return 0

    def get(self, key):
        h = TwitchLogin.owner_hash(key)
        if not h:
            return None
        with self.lock:
            s = self.items.get(h)
            if s is None and os.path.exists(self._path(h)):
                s = self.items[h] = self._make(h)
            return s

    def start(self, key, mod=True):
        h = TwitchLogin.owner_hash(key)
        if not h:
            raise ValueError("Dieser Browser kann sich nicht anmelden")
        with self.lock:
            s = self.items.get(h)
            if s is None:
                if self._count() >= self.MAX and not os.path.exists(self._path(h)):
                    raise ValueError("Es sind schon zu viele Moderatoren angemeldet")
                s = self.items[h] = self._make(h)
        return s.start(mod=bool(mod), owner_key=key)

    def logout(self, key):
        s = self.get(key)
        if s is not None:
            s.logout()
        h = TwitchLogin.owner_hash(key)
        if h:
            with self.lock:
                self.items.pop(h, None)
            try:
                os.remove(self._path(h))
            except OSError:
                pass


class TwitchAccounts:
    NONE_STATUS = {"state": "aus", "login": "", "scopes": [], "error": "", "mod": False, "mod_scope": False, "events": False, "helix_chat": False}
    CHECK_TTL = 300.0

    def __init__(self, box, sessions, check=None, clock=time.time, store=None):
        self.box, self.sessions, self.check, self.clock, self.store = box, sessions, check, clock, store
        self.known = {}
        self.lock = threading.Lock()

    def _is_channel_mod(self, acct):
        if self.check is None:
            return None
        uid = acct.user_id()
        now = self.clock()
        with self.lock:
            hit = self.known.get(uid)
            if hit and now - hit[0] < self.CHECK_TTL:
                return hit[1]
        try:
            ans = self.check(uid)
        except Exception:
            ans = None
        with self.lock:
            if len(self.known) > 100:
                self.known.clear()
            self.known[uid] = (now, ans)
        return ans

    def streamer_name(self):
        name = self.box.login()
        if name:
            return name
        if self.store is not None:
            with self.store.lock:
                return str(self.store.data.get("channel") or "").lower()
        return ""

    def pick(self, key):
        if self.box.ready():
            if self.box.is_owner(key):
                return "owner", self.box
        elif not self.streamer_name():
            return "owner", self.box
        s = self.sessions.get(key)
        if s is not None and self.sessions.demo:
            s.status()
        if s is not None and s.ready():
            if s.status()["mod"] and self._is_channel_mod(s) is not False:
                return "mod", s
            return "user", s
        return "none", None

    def status(self, key):
        role, acct = self.pick(key)
        boxlogin = self.box.login()
        if role == "owner":
            st = self.box.status()
        elif role in ("mod", "user"):
            st = acct.status()
            if role == "user":
                st["mod"] = False
        else:
            h = TwitchLogin.owner_hash(key)
            s = self.sessions.get(key)
            st = dict(self.NONE_STATUS, scopes=[])
            if s is not None and s.state != "aus":
                own = s.status()
                st.update({k: own[k] for k in ("state", "error", "code", "uri", "expires_in") if k in own})
            elif h and self.box.state == "wartet" and (self.box.pending or {}).get("owner") == h:
                own = self.box.status()
                st.update({k: own[k] for k in ("state", "code", "uri", "expires_in") if k in own})
            cf = self.box.claim_fail
            if cf and h and cf.get("owner") == h and st["state"] != "wartet":
                st["state"], st["error"] = "fehler", cf["error"]
        name = self.streamer_name()
        st["role"], st["box_login"], st["streamer"] = role, boxlogin, name
        st["need_channel"] = bool(role == "owner" and not self.box.ready() and not name)
        return st
