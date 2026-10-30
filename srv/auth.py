
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import threading
import time


SESSION_SECONDS = 12 * 3600


REMEMBER_SECONDS = 30 * 24 * 3600


MAX_FAILS, FAIL_WINDOW = 5, 300


MAX_CHECKS = 2


class Auth:

    BELA_JS = ("const b=require(process.argv[1]);let d='';"
               "process.stdin.on('data',c=>d+=c).on('end',()=>"
               "process.exit(b.compareSync(d,process.argv[2])?0:1))")

    DEMO_PASSWORD = "demo"

    def __init__(self, state_dir, bela_config=None, demo=False):
        self.bela_config = bela_config
        self.demo = bool(demo)
        self.dir = state_dir
        self.path = os.path.join(state_dir, "auth.json")
        self.code_path = os.path.join(state_dir, "setup-code")
        self.sessions = {}
        self.store_path = os.path.join(state_dir, "sessions.json")
        self.fails = {}
        self.lock = threading.Lock()
        self.checks = threading.BoundedSemaphore(MAX_CHECKS)
        os.makedirs(state_dir, exist_ok=True)
        self.cfg = None
        try:
            with open(self.path) as f:
                self.cfg = json.load(f)
        except (OSError, ValueError):
            pass
        self.setup_code = None
        if self.demo and not self.bela_hash():
            salt = secrets.token_bytes(16)
            self.cfg = {"salt": salt.hex(), "hash": self._hash(self.DEMO_PASSWORD, salt).hex()}
            try:
                os.remove(self.code_path)
            except OSError:
                pass
        elif not self.cfg and not self.bela_hash() and not self.bela_pending():
            self.setup_code = secrets.token_urlsafe(6)
            fd = os.open(self.code_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(self.setup_code + "\n")
        else:
            try:
                os.remove(self.code_path)
            except OSError:
                pass

    def _cred(self):
        h = self.bela_hash() or ("demo" if self.demo else (self.cfg or {}).get("hash")) or ""
        return hashlib.sha256(("pbcred:" + h).encode()).hexdigest()

    @staticmethod
    def _tok_id(tok):
        return hashlib.sha256(tok.encode()).hexdigest()

    def _remembered(self):
        try:
            with open(self.store_path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    def _save_remembered(self, d):
        tmp = self.store_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self.store_path)

    def bela_hash(self):
        if not self.bela_config:
            return None
        try:
            with open(self.bela_config) as f:
                h = json.load(f).get("password_hash")
        except (OSError, ValueError):
            return None
        return h if isinstance(h, str) and h.startswith("$2") else None

    def bela_ok(self, pw, h):
        mod = os.path.join(os.path.dirname(self.bela_config), "node_modules", "bcrypt")
        try:
            r = subprocess.run(["node", "-e", self.BELA_JS, mod, h],
                               input=pw.encode(), timeout=10,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError("BELABOX-Passwortprüfung nicht möglich")
        if r.returncode not in (0, 1):
            raise RuntimeError("BELABOX-Passwortprüfung nicht möglich")
        return r.returncode == 0

    def bela_pending(self):
        if not self.bela_config or self.bela_hash():
            return False
        return os.path.isdir(os.path.dirname(os.path.abspath(self.bela_config)))

    @property
    def mode(self):
        if self.bela_hash():
            return "belabox"
        if self.demo and self.cfg:
            return "demo"
        if self.cfg:
            return "own"
        return "belabox-wartet" if self.bela_pending() else "own"

    @property
    def configured(self):
        return bool(self.cfg) or self.mode == "belabox"

    @staticmethod
    def _hash(pw, salt):
        return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 300000, dklen=32)

    def throttled(self, ip):
        now = time.time()
        with self.lock:
            f = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
            self.fails[ip] = f
            return len(f) >= MAX_FAILS

    def fail(self, ip):
        with self.lock:
            self.fails.setdefault(ip, []).append(time.time())

    def reserve(self, ip):
        now = time.time()
        with self.lock:
            f = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
            if len(f) >= MAX_FAILS:
                self.fails[ip] = f
                return None
            f.append(now)
            self.fails[ip] = f
            return now

    def unreserve(self, ip, mark):
        with self.lock:
            f = self.fails.get(ip, [])
            if mark in f:
                f.remove(mark)

    def check_password(self, ip, verify):
        mark = self.reserve(ip)
        if mark is None:
            raise PermissionError("Zu viele Versuche, bitte 5 Minuten warten")
        if not self.checks.acquire(blocking=False):
            self.unreserve(ip, mark)
            raise PermissionError("Die Box prüft gerade andere Anmeldungen, bitte gleich noch einmal versuchen")
        try:
            good = verify()
        except BaseException:
            self.unreserve(ip, mark)
            raise
        finally:
            self.checks.release()
        if good:
            self.unreserve(ip, mark)
        return good

    WAITING_MSG = "Auf der BELABOX ist noch kein Passwort gesetzt. Bitte zuerst in der BELABOX-Oberfläche eines festlegen."

    def set_password(self, code, pw, ip):
        if self.mode == "belabox-wartet":
            raise ValueError(self.WAITING_MSG)
        if self.configured or not self.setup_code:
            raise ValueError("Passwort ist schon gesetzt")
        if not self.check_password(ip, lambda: hmac.compare_digest(code or "", self.setup_code)):
            raise ValueError("Setup-Code falsch")
        if len(pw or "") < 10:
            raise ValueError("Passwort: mindestens 10 Zeichen")
        salt = secrets.token_bytes(16)
        self.cfg = {"salt": salt.hex(), "hash": self._hash(pw, salt).hex()}
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.cfg, f)
        os.replace(tmp, self.path)
        self.setup_code = None
        try:
            os.remove(self.code_path)
        except OSError:
            pass

    def login(self, pw, ip, remember=False):
        if not self.configured:
            raise ValueError(self.WAITING_MSG if self.mode == "belabox-wartet" else "Noch kein Passwort gesetzt")
        bh = self.bela_hash()
        if bh:
            verify = lambda: self.bela_ok(pw or "", bh)
        else:
            verify = lambda: hmac.compare_digest(self._hash(pw or "", bytes.fromhex(self.cfg["salt"])), bytes.fromhex(self.cfg["hash"]))
        if not self.check_password(ip, verify):
            raise ValueError("Passwort falsch")
        tok = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.sessions = {t: e for t, e in self.sessions.items() if e > now}
            self.sessions[tok] = now + SESSION_SECONDS
            if remember:
                keep = {k: v for k, v in self._remembered().items()
                        if isinstance(v, list) and len(v) == 2 and v[0] > now and v[1] == self._cred()}
                keep[self._tok_id(tok)] = [now + REMEMBER_SECONDS, self._cred()]
                try:
                    self._save_remembered(keep)
                except OSError:
                    pass
        return tok

    def valid(self, tok):
        if not tok:
            return False
        with self.lock:
            if self.sessions.get(tok, 0) > time.time():
                return True
            e = self._remembered().get(self._tok_id(tok))
            return bool(isinstance(e, list) and len(e) == 2 and e[0] > time.time() and e[1] == self._cred())

    def logout(self, tok):
        with self.lock:
            self.sessions.pop(tok or "", None)
            r = self._remembered()
            if r.pop(self._tok_id(tok or ""), None) is not None:
                try:
                    self._save_remembered(r)
                except OSError:
                    pass
