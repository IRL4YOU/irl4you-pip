
import json
import os
import re
import subprocess
import threading
import time

from srv.util import read, ttl_cached, ttl_cached_drop
from srv.versions import belacoder_running


class Power:
    ACTIONS = ("poweroff", "reboot")

    def __init__(self, state_dir, demo, send):
        self.req = os.path.join(state_dir, "power-request")
        self.demo, self.send = demo, send

    def status(self):
        sending = False if self.demo else bool(belacoder_running() or self.send._active())
        return {"helper_installed": self.demo or os.path.exists("/etc/systemd/system/pipbox-power.path"), "sending": sending}

    def request(self, action, confirm):
        if action not in self.ACTIONS:
            raise ValueError("Unbekannte Aktion")
        if confirm is not True:
            raise ValueError("Bestätigung fehlt")
        if not self.status()["helper_installed"]:
            raise ValueError("Der Helfer ist nicht installiert (install.sh erneut ausführen)")
        if self.demo:
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n")


class AutoStart:
    WAIT_S = 600
    RETRY_S = 20

    def __init__(self, state_dir, send, demo=False, wait_s=None, poll_s=5.0):
        self.path = os.path.join(state_dir, "autostart.json")
        self.send, self.demo = send, demo
        self.wait_s = self.WAIT_S if wait_s is None else wait_s
        self.poll_s = poll_s
        self.lock = threading.Lock()
        self.cancel_ev = threading.Event()
        self.data = {"enabled": False, "boot": ""}
        try:
            with open(self.path) as f:
                d = json.load(f)
            self.data["enabled"] = d.get("enabled") is True
            self.data["boot"] = str(d.get("boot", ""))[:64]
        except (OSError, ValueError):
            pass
        self.phase, self.message, self.until = "aus", "", None

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    @staticmethod
    def boot_id():
        return (read("/proc/sys/kernel/random/boot_id", "demo") or "demo").strip()

    def enabled(self):
        return self.data["enabled"]

    def set_enabled(self, on):
        if not isinstance(on, bool):
            raise ValueError("ja oder nein")
        with self.lock:
            self.data["enabled"] = on
            self._save()
        if not on:
            self.cancel("ausgeschaltet")

    def cancel(self, why="abgebrochen"):
        with self.lock:
            if self.phase == "wartet":
                self.cancel_ev.set()
                self.phase, self.message = "abgebrochen", why
                self.data["boot"] = self.boot_id()
                self._save()

    def status(self):
        return {"enabled": self.data["enabled"], "phase": self.phase, "message": self.message, "until": self.until}

    def _finish(self, phase, message=""):
        with self.lock:
            if self.phase == "wartet":
                self.phase, self.message = phase, message
            self.data["boot"] = self.boot_id()
            self._save()

    def run(self):
        if not self.data["enabled"]:
            return
        if self.data["boot"] == self.boot_id():
            with self.lock:
                self.phase = "erledigt"
            return
        with self.lock:
            self.phase, self.message = "wartet", "Wartet auf eine sendende Kamera"
            self.until = time.time() + self.wait_s
        deadline = time.monotonic() + self.wait_s
        last_try, reasons = None, []
        while time.monotonic() < deadline and not self.cancel_ev.is_set():
            try:
                st = self.send.status()
            except Exception:
                st = {}
            if st.get("active"):
                self._finish("gestartet", "Läuft")
                return
            reasons = st.get("reasons") or reasons
            if st.get("can_start") and (last_try is None or time.monotonic() - last_try >= self.RETRY_S):
                last_try = time.monotonic()
                try:
                    self.send.request("start", True)
                    with self.lock:
                        self.message = "Startet"
                except Exception as e:
                    reasons = [str(e)]
            self.cancel_ev.wait(self.poll_s)
        if self.cancel_ev.is_set():
            return
        self._finish("aufgegeben", "Automatischer Start aufgegeben: " + ("; ".join(reasons) if reasons else "keine sendende Kamera"))


class LogMode:
    MODES = ("sparsam", "ausfuehrlich")
    FILE = "/etc/pipbox/logmode"

    def __init__(self, state_dir, demo):
        self.req = os.path.join(state_dir, "logmode-request")
        self.demo = demo
        self.fake = "ausfuehrlich"

    def status(self):
        if self.demo:
            return {"mode": self.fake, "helper_installed": True}
        mode = (read(self.FILE, "") or "").strip()
        return {"mode": mode if mode in self.MODES else "ausfuehrlich",
                "helper_installed": os.path.exists("/etc/systemd/system/pipbox-logmode.path")}

    def request(self, mode):
        if mode not in self.MODES:
            raise ValueError("Unbekannter Modus")
        if not self.status()["helper_installed"]:
            raise ValueError("Der Helfer ist nicht installiert (Software-Update einspielen oder install.sh erneut ausführen)")
        if self.demo:
            self.fake = mode
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(mode + "\n")


class BtDriver:
    STATUS = "/run/pipbox-btdriver/status.json"
    UNIT = "/etc/systemd/system/pipbox-btdriver.path"
    STATES = ("working", "waiting", "failed", "unsupported", "ok", "none")
    CHIP_STATUS = "/run/pipbox-btchip/status.json"
    CHIP_UNIT = "/etc/systemd/system/pipbox-btchip.path"
    CHIP_STATES = ("working", "waiting", "failed", "unsupported", "ok")

    def __init__(self, state_dir, demo):
        self.req = os.path.join(state_dir, "btdriver-request")
        self.chip_req = os.path.join(state_dir, "btchip-request")
        self.demo = demo
        self.fake = {}

    def status(self):
        if self.demo:
            return {"helper_installed": True, "state": "", "message": "", **self.fake}
        out = {"helper_installed": os.path.exists(self.UNIT), "state": "", "message": ""}
        try:
            with open(self.STATUS) as f:
                raw = json.load(f)
            if raw.get("state") in self.STATES:
                out.update(state=raw["state"], message=str(raw.get("message", ""))[:300], manual=bool(raw.get("manual")),
                           step=int(raw.get("step") or 0), steps=int(raw.get("steps") or 0), step_text=str(raw.get("step_text", ""))[:80],
                           time=int(raw.get("time") or 0))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        chip = self.chip_status()
        if chip:
            out["chip"] = chip
        return out

    def chip_status(self):
        if self.demo:
            return None
        try:
            with open(self.CHIP_STATUS) as f:
                raw = json.load(f)
            if raw.get("applies") is True and raw.get("state") in self.CHIP_STATES:
                return {"state": raw["state"], "message": str(raw.get("message", ""))[:300], "name": str(raw.get("name", ""))[:80],
                        "manual": bool(raw.get("manual")), "time": int(raw.get("time") or 0)}
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return None

    def request(self):
        st = self.status()
        if not st["helper_installed"]:
            raise ValueError("Der Helfer ist nicht installiert (Software-Update einspielen oder install.sh erneut ausführen)")
        if st.get("state") == "working" and time.time() - st.get("time", 0) < 1800:
            raise ValueError("Der Treiber wird gerade eingerichtet")
        chip = st.get("chip") or {}
        if chip.get("state") == "working" and time.time() - chip.get("time", 0) < 600:
            raise ValueError("Der Bluetooth-Chip auf dem Board wird gerade eingerichtet")
        if self.demo:
            self.fake = {"state": "working", "manual": True, "step": 2, "steps": 5, "step_text": "Treiber wird gebaut", "message": "Treiber für TP-Link UB500 wird gebaut und eingerichtet (einige Minuten) …"}
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("install\n")
        if os.path.exists(self.CHIP_UNIT):
            fd = os.open(self.chip_req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write("install\n")


class LogBundle:
    DIR = "/run/pipbox-logs"
    FILE = DIR + "/bundle.txt"
    STATUS = DIR + "/status.json"
    HELPER = "/etc/systemd/system/pipbox-logs.path"
    MAX_BYTES = 4_000_000
    WAIT_SECONDS = 150

    def __init__(self, state_dir, demo):
        self.req = os.path.join(state_dir, "logs-request")
        self.demo = demo
        self.requested = 0.0
        self.fake = None

    def installed(self):
        return self.demo or os.path.exists(self.HELPER)

    def status(self):
        out = {"helper_installed": self.installed(), "state": "idle", "message": "", "size": 0}
        if self.demo:
            if self.fake:
                out.update(state="done", message="Fertig", size=len(self.fake.encode()), time=int(self.requested))
            return out
        try:
            with open(self.STATUS) as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = None
        waiting = self.requested and time.time() - self.requested < self.WAIT_SECONDS
        if not isinstance(st, dict) or (self.requested and int(st.get("time", 0) or 0) < int(self.requested)):
            if self.requested:
                out["state"], out["message"] = ("working", "Warte auf den Helfer …") if waiting else ("error", "Keine Antwort vom Helfer")
            return out
        state = st.get("state") if st.get("state") in ("working", "done", "error") else "error"
        out.update(state=state, message=str(st.get("message", ""))[:200], time=int(st.get("time", 0) or 0))
        if state == "done":
            try:
                out["size"] = os.stat(self.FILE).st_size
            except OSError:
                out["state"], out["message"] = "error", "Datei fehlt"
        elif state == "working" and not waiting:
            out["state"], out["message"] = "error", "Zeitüberschreitung beim Sammeln"
        return out

    def request(self):
        if not self.installed():
            raise ValueError("Der Helfer ist nicht installiert (Software-Update einspielen oder install.sh erneut ausführen)")
        if self.status()["state"] == "working" and time.time() - self.requested < self.WAIT_SECONDS:
            return
        self.requested = time.time()
        if self.demo:
            self.fake = ("IRL4YOU BOX Protokolle (Vorschau)\n\n===== Journal pipbox-dji =====\n"
                         "2026-10-04T17:21:22+0000 INFO pipbox-dji: <MAC-1 AC:DE:48>: Statusnachricht\n")
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("collect\n")

    def content(self):
        if self.demo:
            return self.fake.encode() if self.fake else None
        if self.status()["state"] != "done":
            return None
        try:
            with open(self.FILE, "rb") as f:
                return f.read(self.MAX_BYTES)
        except OSError:
            return None


class Developer:
    ACTIONS = ("start", "stop", "check", "reset")
    STATUS = "/run/pipbox-ssh/status.json"
    HELPER = "/etc/systemd/system/pipbox-ssh.path"
    USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")

    def __init__(self, state_dir, demo, bela_config=None):
        self.req = os.path.join(state_dir, "ssh-request")
        self.pass_file = os.path.join(state_dir, "ssh-pass.json")
        self.demo = demo
        self.bela_dir = os.path.dirname(os.path.abspath(bela_config)) if bela_config else None
        self.fake_active = False
        self.fake_pass = "Demo-Passwort-1234"
        self.fake_time = 0

    def _json(self, name):
        if not self.bela_dir:
            return None
        try:
            with open(os.path.join(self.bela_dir, name)) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        return d if isinstance(d, dict) else None

    def ssh_user(self):
        u = (self._json("setup.json") or {}).get("ssh_user")
        return u if isinstance(u, str) and self.USER_RE.fullmatch(u) else ""

    def _ours(self):
        try:
            with open(self.pass_file) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        user = self.ssh_user()
        if isinstance(d, dict) and isinstance(d.get("password"), str) and d["password"] and isinstance(d.get("user"), str) and (not user or d["user"] == user):
            return d
        return None

    def password_created(self):
        if self._ours():
            return True
        c = self._json("config.json")
        return None if c is None else bool(c.get("ssh_pass"))

    @staticmethod
    def _has_unit():
        try:
            return subprocess.run(["systemctl", "cat", "ssh.service"], capture_output=True, timeout=4).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _systemctl(*args):
        try:
            r = subprocess.run(["systemctl", *args, "ssh"], capture_output=True, text=True, timeout=4)
            return r.returncode, r.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return 1, ""

    def status(self):
        if self.demo:
            return {"helper_installed": True, "available": True, "user": "user", "can_reset": True, "active": self.fake_active, "enabled": True,
                    "password_created": bool(self.fake_pass), "password_state": "generated" if self.fake_pass else "unknown",
                    "state": "idle", "message": "", "time": self.fake_time}
        try:
            with open(self.STATUS) as f:
                h = json.load(f)
        except (OSError, ValueError):
            h = {}
        has_unit = ttl_cached("ssh_unit", 30.0, self._has_unit)
        active = ttl_cached("ssh_active", 2.0, lambda: self._systemctl("is-active")[1] == "active")
        enabled = ttl_cached("ssh_enabled", 30.0, lambda: self._systemctl("is-enabled")[1] == "enabled")
        user = self.ssh_user()
        return {"helper_installed": os.path.exists(self.HELPER), "available": bool(has_unit), "user": user, "can_reset": bool(user),
                "active": active, "enabled": enabled, "password_created": self.password_created(),
                "password_state": h.get("password_state") if h.get("password_state") in ("generated", "own", "unknown") else None,
                "state": h.get("state", "idle"), "message": str(h.get("message", ""))[:200], "time": h.get("time", 0)}

    def request(self, action, confirm):
        if action not in self.ACTIONS:
            raise ValueError("Unbekannte Aktion")
        st = self.status()
        if not st["helper_installed"]:
            raise ValueError("Der Helfer ist nicht installiert (Software-Update einspielen oder install.sh erneut ausführen)")
        if action != "reset" and action != "check" and not st["available"]:
            raise ValueError("Auf dieser Box gibt es keinen SSH-Dienst")
        if st["state"] == "working" and time.time() - st.get("time", 0) < 60:
            raise ValueError("Es läuft schon eine Aktion")
        if action == "reset":
            if not st["can_reset"]:
                raise ValueError("Für diese Box ist kein SSH-Benutzer eingerichtet")
            if confirm is not True:
                raise ValueError("Bestätigung fehlt: Das alte Passwort gilt danach nicht mehr")
        if self.demo:
            self.fake_time += 1
            if action in ("start", "stop"):
                self.fake_active = action == "start"
            elif action == "reset":
                self.fake_pass = "Neu-" + str(self.fake_time) + "-Demo-Passwort"
            return
        ttl_cached_drop("ssh_active")
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n")

    def password(self):
        if self.demo:
            if not self.fake_pass:
                raise ValueError("Für SSH wurde noch kein Passwort erzeugt")
            return {"user": "user", "password": self.fake_pass, "state": "generated"}
        ours = self._ours()
        if ours:
            return {"user": self.ssh_user() or ours["user"], "password": ours["password"], "state": self.status().get("password_state")}
        c = self._json("config.json")
        pw = c.get("ssh_pass") if c else None
        if not isinstance(pw, str) or not pw:
            raise ValueError("Für SSH wurde noch kein Passwort erzeugt. Es entsteht beim ersten Einschalten oder mit „Passwort zurücksetzen“.")
        return {"user": self.ssh_user(), "password": pw, "state": self.status().get("password_state")}
