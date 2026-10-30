
import fcntl
import json
import os
import re
import threading
import time
import urllib.request

from srv.util import BASE_DIR, read
from srv.versions import VERSION_RE, belacoder_running, installed_versions, vkey


class SwUpdate:
    CHECK_EVERY = 6 * 3600
    FRESH_AFTER = 5 * 60
    RETRY_AFTER_ERROR = 30 * 60
    EARLY_SECONDS = 30 * 60
    RETRY_EARLY = 3 * 60
    RAW = "https://raw.githubusercontent.com/IRL4YOU/irl4you-pip/%s/"
    API = "https://api.github.com/repos/IRL4YOU/irl4you-pip/releases?per_page=30"
    STATUS = "/run/pipbox-swupdate/status.json"
    BACKUP = "/var/lib/pipbox-backup"
    STAGE = ""
    CHANGELOG_MAX = 600_000
    HISTORY_TTL = 30 * 60

    def __init__(self, state_dir, demo, send):
        self.req = os.path.join(state_dir, "swupdate-request")
        self.demo, self.send = demo, send
        self.lock = threading.Lock()
        self.cache, self.cache_t = None, 0.0
        self.hist, self.hist_t = None, 0.0
        self.started = time.time()
        self.rel_cache, self.rel_t = [], 0.0
        here = BASE_DIR
        self.version = (read(os.path.join(here, "VERSION"), "") or "0.0.0").strip()
        self.fake = {}
        self.fake_t0 = 0.0

    CONTENTS = "https://api.github.com/repos/IRL4YOU/irl4you-pip/contents/%s?ref=%s"

    @property
    def channel(self):
        return "test" if re.search(r"-test\d+$", self.version) else "stable"

    def _vget(self, name, limit, ref):
        return self._get(name, limit) if ref == "main" else self._get(name, limit, ref)

    def _get(self, name, limit, ref="main"):
        last = None
        for url, extra in ((self.CONTENTS % (name, ref), {"Accept": "application/vnd.github.raw+json"}), ((self.RAW % ref) + name, {})):
            try:
                req = urllib.request.Request(url, headers=dict(extra, **{"User-Agent": "irl4you-box"}))
                with urllib.request.urlopen(req, timeout=8) as r:
                    return r.read(limit + 1)[:limit].decode("utf-8", "replace")
            except OSError as e:
                last = e
        raise last

    @staticmethod
    def _first_section(text):
        lines, out, started = text.splitlines(), [], False
        for l in lines:
            if l.startswith("## "):
                if started:
                    break
                started = True
            if started:
                out.append(l.rstrip())
            if len(out) >= 25:
                break
        return "\n".join(out)

    @staticmethod
    def _oldest(text):
        out = None
        for m in re.finditer(r"(?m)^##\s+(\d+)\.(\d+)\.(\d+)", text):
            v = tuple(int(x) for x in m.groups())
            out = v if out is None or v < out else out
        return out

    def _changelog(self, current=None, ref="main"):
        text = self._vget("CHANGELOG.md", self.CHANGELOG_MAX, ref)
        oldest = self._oldest(text)
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)", current or "")
        cur = tuple(int(x) for x in m.groups()) if m else None
        need = current is None or cur is None or (oldest is not None and cur < oldest)
        if need:
            try:
                text = text.rstrip("\n") + "\n\n" + self._vget(b"CHANGELOG-Archiv.md".decode(), self.CHANGELOG_MAX, ref)
            except OSError:
                pass
        return text

    @classmethod
    def _sections_since(cls, text, current, max_sections=100, max_lines=4000, max_chars=200_000):
        def num(v):
            m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v or "")
            return tuple(int(x) for x in m.groups()) if m else None
        cur = num(current)
        out, keep, sections, chars = [], False, 0, 0
        for l in text.splitlines():
            if l.startswith("## "):
                m = re.match(r"^##\s+(\d+\.\d+\.\d+)", l)
                v = num(m.group(1)) if m else None
                keep = v is not None and cur is not None and v > cur
                if keep:
                    sections += 1
                    if sections > max_sections:
                        break
            if keep:
                out.append(l.rstrip())
                chars += len(l) + 1
                if len(out) >= max_lines or chars >= max_chars:
                    break
        return "\n".join(out) if out else cls._first_section(text)

    def check(self, force=False, fresh=False):
        with self.lock:
            early = time.time() - self.started < self.EARLY_SECONDS
            wait = (self.RETRY_EARLY if early else self.RETRY_AFTER_ERROR) if self.cache and self.cache.get("error") else self.CHECK_EVERY
            if self.cache and self.cache.get("stale"):
                wait = self.RETRY_EARLY
            if fresh:
                wait = min(wait, self.FRESH_AFTER)
            if not force and self.cache and (time.time() - self.cache_t < wait or self.send._active()):
                return self.cache
            if not force and not self.cache and not self.demo and self.send._active():
                return {"checked_at": None}
        res = {"checked_at": int(time.time())}
        if self.demo:
            res.update(latest="0.9.1", notes="## 0.9.1 (Demo)\n- Beispiel für eine neue Version.", test_latest="0.9.2-test1")
        else:
            try:
                ref = "test" if self.channel == "test" else "main"
                v = self._vget("VERSION", 64, ref).strip()
                if not VERSION_RE.match(v):
                    raise ValueError("ungültige Versionsnummer")
                res["latest"] = v
                other = "main" if ref == "test" else "test"
                try:
                    o = self._vget("VERSION", 64, other).strip()
                    res["test_latest" if other == "test" else "stable_latest"] = o if VERSION_RE.match(o) else ""
                except (OSError, TypeError):
                    pass
                if vkey(v) < vkey(self.version):
                    res["latest"], res["stale"] = self.version, True
                try:
                    res["notes"] = self._sections_since(self._changelog(self.version, ref), self.version)
                except OSError:
                    res["notes"] = ""
            except (OSError, ValueError):
                res["error"] = "GitHub ist nicht erreichbar oder lieferte keine gültige Versionsnummer."
        with self.lock:
            self.cache, self.cache_t = res, time.time()
        return res

    def history(self):
        with self.lock:
            if self.hist and time.time() - self.hist_t < self.HISTORY_TTL:
                return self.hist
        if self.demo:
            res = {"text": "## 0.9.1 (Demo)\n- Beispiel für eine neue Version.\n\n## 0.9.0 (Demo)\n- Erste Version.", "error": ""}
        else:
            try:
                res = {"text": self._changelog(), "error": ""}
            except OSError:
                with self.lock:
                    return self.hist or {"text": "", "error": "GitHub ist nicht erreichbar."}
        with self.lock:
            self.hist, self.hist_t = res, time.time()
        return res

    def auto_loop(self):
        time.sleep(30)
        while True:
            try:
                self.check()
            except Exception as e:
                print("swupdate auto_check:", e)
            time.sleep(60 if time.time() - self.started < self.EARLY_SECONDS else 15 * 60)

    def releases(self, force=False):
        if not force and (time.time() - self.rel_t < self.CHECK_EVERY or (not self.demo and self.send._active())):
            return self.rel_cache
        out = []
        if self.demo:
            out = [{"version": "0.9.1", "date": "2026-10-02", "notes": "Demo"}, {"version": "0.9.0", "date": "2026-10-01", "notes": "Demo"}]
        else:
            try:
                req = urllib.request.Request(self.API, headers={"User-Agent": "irl4you-box", "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read(400000))
                for x in data if isinstance(data, list) else []:
                    v = str(x.get("tag_name", "")).lstrip("v")
                    if VERSION_RE.match(v) and not x.get("draft") and not x.get("prerelease"):
                        out.append({"version": v, "date": str(x.get("published_at", ""))[:10],
                                    "notes": str(x.get("body") or "")[:300]})
            except (OSError, ValueError):
                out = self.rel_cache
        with self.lock:
            self.rel_cache, self.rel_t = out, time.time()
        return out

    def backups(self):
        if self.demo:
            return ["0.9.0"]
        out = []
        try:
            for n in os.listdir(self.BACKUP):
                p = os.path.join(self.BACKUP, n)
                if VERSION_RE.match(n) and os.path.isdir(os.path.join(p, "opt-pipbox")):
                    out.append((os.stat(p).st_mtime, n))
            legacy = (read(os.path.join(self.BACKUP, "VERSION"), "") or "").strip()
            if VERSION_RE.match(legacy) and os.path.isdir(os.path.join(self.BACKUP, "opt-pipbox")) \
                    and legacy not in [n for _, n in out]:
                out.append((os.stat(os.path.join(self.BACKUP, "VERSION")).st_mtime, legacy))
        except OSError:
            pass
        return [n for _, n in sorted(out, reverse=True)]

    def versions(self, force=False):
        local = self.backups()
        rel = {r["version"]: r for r in self.releases(force)}
        res = []
        for v in sorted(set(local) | set(rel) | {self.version}, key=vkey, reverse=True):
            res.append({"version": v, "local": v in local, "github": v in rel, "current": v == self.version,
                        "date": rel.get(v, {}).get("date", ""), "notes": rel.get(v, {}).get("notes", ""),
                        "relation": "aktuell" if v == self.version else ("neuer" if vkey(v) > vkey(self.version) else "älter")})
        return res

    @staticmethod
    def _pct(st):
        p = st.get("progress")
        return p if isinstance(p, int) and not isinstance(p, bool) and 0 <= p <= 100 else 0

    def demo_fake(self):
        t0 = self.fake_t0
        steps = ((0, 2, "Lade die neue Version von GitHub"), (2, 22, "Prüfe das Archiv"), (3, 30, "Sichere die jetzige Version"),
                 (4, 36, "Installiere Version %s" % "9.9.9"), (6, 58, "Kopiere Programme und Oberfläche"), (8, 78, "Prüfe den SRTLA-Sender (wird neu gebaut, wenn er sich geändert hat)"),
                 (10, 95, "Starte die Dienste neu"))
        age = time.time() - t0
        if age >= 12:
            return {"state": "done", "message": "Demo: Update vorgetäuscht.", "time": int(time.time()), "progress": 100}
        cur = [x for x in steps if x[0] <= age][-1]
        return {"state": "installing", "step": cur[2], "progress": cur[1], "time": int(time.time())}

    def progress_public(self):
        if self.demo:
            st = self.demo_fake() if self.fake_t0 else {}
        else:
            try:
                with open(self.STATUS) as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {}
        state = st.get("state") if st.get("state") in ("installing", "done", "rolledback", "failed", "refused") else "idle"
        if state != "installing" and (not st.get("time") or time.time() - st["time"] > 6 * 3600):
            state = "idle"
        step = st.get("step", "")
        return {"state": state, "step": step if state == "installing" and isinstance(step, str) else "", "progress": self._pct(st)}

    def status(self, force=False, fresh=False):
        chk = self.check(force, fresh)
        if self.demo:
            st = (self.demo_fake() if self.fake_t0 else dict(self.fake)) or {}
        else:
            try:
                with open(self.STATUS) as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {}
        latest = chk.get("latest")
        out = {"current": self.version, "stage": self.STAGE, "latest": latest, "notes": chk.get("notes", ""),
               "error": chk.get("error", ""), "checked_at": chk.get("checked_at"),
               "newer": bool(latest and vkey(latest) > vkey(self.version)),
               "state": st.get("state", "idle"), "step": st.get("step", ""), "message": st.get("message", ""),
               "frm": st.get("frm", ""), "to": st.get("to", ""), "progress": self._pct(st)}
        if st.get("state") in ("installing", "refused", "done", "rolledback", "failed") and st.get("time") \
                and st["state"] != "installing" and time.time() - st["time"] > 6 * 3600:
            out["state"], out["message"] = "idle", ""
        bk = [v for v in self.backups() if v != self.version]
        out["backup_version"] = bk[0] if bk else ""
        out["versions"] = self.versions(force)
        out["sending"] = False if self.demo else bool(belacoder_running() or self.send._active())
        out["helper_installed"] = self.demo or os.path.exists("/etc/systemd/system/pipbox-swupdate.path")
        out["channel"] = self.channel
        out["test_latest"] = chk.get("test_latest", "") if self.channel == "stable" else (latest or "")
        out["stable_latest"] = chk.get("stable_latest", "") if self.channel == "test" else (latest or "")
        out["bridge"] = False
        sl = out["stable_latest"]
        if self.channel == "test" and sl and VERSION_RE.match(sl) and "-" not in sl and latest and vkey(sl) > vkey(self.version) \
                and vkey(sl) >= vkey(latest.partition("-")[0]):
            out["bridge"] = True
            out["latest"], out["newer"] = sl, True
            out["notes"] = "Die Testphase des Datenschutz-Tests ist beendet. Dieses Update führt zurück auf die stabile Version; die Erkennung ist dort eine eigene Karte (Datenschutz)."
        return out

    def request(self, action, confirm, version=None, older=False):
        if action not in ("install", "rollback", "switch", "test", "stable"):
            raise ValueError("Unbekannte Aktion")
        if confirm is not True:
            raise ValueError("Bestätigung fehlt")
        st = self.status()
        if action == "install" and self.channel == "test":
            action = "stable" if st.get("bridge") else "test"
        if not st["helper_installed"]:
            raise ValueError("Der Update-Helfer ist nicht installiert (install.sh erneut ausführen)")
        if st["sending"]:
            raise ValueError("Es wird gerade gesendet. Bitte zuerst die Übertragung beenden.")
        if st["state"] == "installing":
            raise ValueError("Ein Update läuft bereits")
        if action == "install" and not st["newer"]:
            raise ValueError("Es gibt keine neuere Version")
        if action == "test" and (not re.search(r"-test\d+$", st["test_latest"] or "") or (self.channel == "test" and not st["newer"])):
            raise ValueError("Es gibt keine neuere Testversion")
        if action == "stable" and self.channel != "test":
            raise ValueError("Es läuft keine Testversion")
        if action == "rollback" and not st["backup_version"]:
            raise ValueError("Es gibt keine gesicherte Version")
        if action == "switch":
            v = str(version or "").strip().lstrip("v")
            if not VERSION_RE.match(v):
                raise ValueError("Bitte eine Version wie 0.9.0 angeben")
            known = {x["version"]: x for x in st["versions"]}
            if v not in known or not (known[v]["local"] or known[v]["github"]):
                raise ValueError(f"Version {v} ist weder gesichert noch auf GitHub veröffentlicht")
            if v == self.version:
                raise ValueError(f"Version {v} ist schon installiert")
            if vkey(v) < vkey(self.version) and older is not True:
                raise ValueError("Das ist eine ältere Version. Bitte ausdrücklich bestätigen.")
            version = v
        if self.demo:
            self.fake_t0 = time.time()
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(action + "\n" + (version + "\n" if action == "switch" else ""))


class Updates:
    MODES = ("check", "dry", "run", "reboot")

    def __init__(self, state_dir, demo):
        self.demo = demo
        self.req = os.path.join(state_dir, "update-request")
        self.status_path = os.path.join(state_dir, "update-status.json")
        self.log_path = os.path.join(state_dir, "update.log")
        self.cancel_path = os.path.join(state_dir, "update-cancel")
        self.fake_cancel = False
        self.fake = {}
        self.lock = threading.Lock()
        self.last_try = 0.0

    def boot_id(self):
        return (read("/proc/sys/kernel/random/boot_id", "demo") or "demo").strip()

    def reboot_done(self, st):
        rb = st.get("reboot_boot_id")
        if rb and rb != self.boot_id():
            return True
        try:
            up = float((read("/proc/uptime", "") or "0").split()[0])
            return bool(st.get("finished")) and st["finished"] < time.time() - up
        except (ValueError, IndexError, TypeError):
            return False

    def status(self):
        if self.demo:
            st = dict(self.fake) or {"state": "never"}
            versions = {"belabox-linux-rk3588": "20241120-2", "belaui": "20250518-5",
                        "belacoder": "20250518-1"}
            log = st.pop("log", "")
            kernel = "5.10.160-belabox (Demo)"
        else:
            try:
                with open(self.status_path) as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {"state": "never"}
            versions = installed_versions()
            log = ""
            try:
                with open(self.log_path) as f:
                    log = "".join(f.readlines()[-60:])
            except OSError:
                pass
            kernel = os.uname().release
        if not self.demo and st.get("state") == "running" and time.time() - (st.get("started") or 0) > self.STALE_AFTER and not self.helper_alive():
            st = dict(st, state="failed", finished=int(time.time()), message="Das Update wurde unterbrochen (Neustart der Box oder Zeitgrenze des Systems). Bitte erneut starten.")
        st["reboot_pending"] = bool(st.get("reboot_required")) and st.get("reboot_boot_id") == self.boot_id()
        if st.get("state") == "rebooting" and self.reboot_done(st):
            st["state"] = "done"
            st["message"] = "Die Box ist nach dem Neustart wieder oben."
        st["versions"] = versions
        st["kernel_running"] = kernel
        st["streaming"] = belacoder_running() if not self.demo else False
        st["helper_installed"] = self.demo or os.path.exists("/etc/systemd/system/pipbox-update.path")
        st["cancel_pending"] = os.path.lexists(self.cancel_path)
        st["cancel_in"] = max(0, int(self.STALL_S - (time.time() - (st.get("activity") or st.get("started") or 0)))) if st.get("state") == "running" else 0
        st["log"] = log
        return st

    def request(self, mode, confirm):
        if mode == "cancel":
            return self.cancel()
        if mode not in self.MODES:
            raise ValueError("Unbekannte Aktion")
        if mode in ("run", "reboot") and not confirm:
            raise ValueError("Bestätigung fehlt")
        cur = self.status()
        if cur.get("state") == "running":
            raise ValueError("Es läuft bereits eine Aktion")
        if mode in ("run", "reboot") and cur["streaming"]:
            raise ValueError("Übertragung läuft. Bitte zuerst den Stream beenden.")
        if not cur["helper_installed"]:
            raise ValueError("Update-Helfer ist nicht installiert")
        if self.demo:
            threading.Thread(target=self._fake, args=(mode,), daemon=True).start()
            return
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(mode + "\n")

    def cancel(self):
        st = self.status()
        if st.get("state") != "running":
            raise ValueError("Es läuft kein Update")
        if st.get("installing"):
            raise ValueError("Abbrechen ist jetzt nicht mehr möglich: Die Pakete werden eingerichtet.")
        if st.get("cancel_in"):
            raise ValueError("Abbrechen geht erst, wenn das Update 10 Minuten lang nicht weiterkommt.")
        if self.demo:
            self.fake_cancel = True
            return
        fd = os.open(self.cancel_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)

    def live_block(self):
        if self.demo:
            return "Ein Systemupdate läuft gerade. Live gehen ist erst danach möglich." if self.fake.get("state") == "running" and self.fake.get("mode") == "run" else None
        try:
            with open(self.status_path) as f:
                st = json.load(f)
        except (OSError, ValueError):
            return None
        if st.get("state") == "running" and st.get("mode") == "run" and (time.time() - (st.get("started") or 0) <= self.STALE_AFTER or self.helper_alive()):
            return "Ein Systemupdate läuft gerade. Live gehen ist erst danach möglich."
        return None

    STALL_S = 600
    LOCK_PATH = "/run/pipbox-update.lock"
    STALE_AFTER = 30

    def helper_alive(self):
        try:
            fd = os.open(self.LOCK_PATH, os.O_RDONLY)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except OSError:
            return True
        finally:
            os.close(fd)

    AUTO_EVERY = 6 * 3600
    AUTO_RETRY = 3600
    AUTO_AFTER_BOOT = 2 * 60
    EARLY_UPTIME = 30 * 60
    AUTO_RETRY_EARLY = 5 * 60

    @classmethod
    def auto_check_due(cls, st, now, uptime, streaming, request_pending, last_try, helper=True):
        if not helper or streaming or request_pending or uptime < cls.AUTO_AFTER_BOOT:
            return False
        if st.get("state") in ("running", "rebooting"):
            return False
        retry = cls.AUTO_RETRY_EARLY if uptime < cls.EARLY_UPTIME else cls.AUTO_RETRY
        if last_try and now - last_try < retry:
            return False
        last = st.get("last_check") or 0
        if last < now - uptime:
            return True
        return now - last >= cls.AUTO_EVERY

    def auto_check(self, now=None, last_try=None, present=False):
        if self.demo:
            return False
        try:
            uptime = float((read("/proc/uptime", "") or "0").split()[0])
        except (ValueError, IndexError):
            uptime = 0.0
        if present:
            uptime = max(uptime, float(self.AUTO_AFTER_BOOT))
        st = self.status()
        if last_try is None:
            last_try = self.last_try
        if not self.auto_check_due(st, now or time.time(), uptime, st.get("streaming", False), os.path.exists(self.req), last_try,
                                   helper=st.get("helper_installed", False)):
            return False
        fd = os.open(self.req, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("autocheck\n")
        self.last_try = time.time()
        return True

    def auto_loop(self):
        time.sleep(30)
        while True:
            try:
                self.auto_check()
            except Exception as e:
                print("auto_check:", e)
            try:
                early = float((read("/proc/uptime", "") or "0").split()[0]) < self.EARLY_UPTIME
            except (ValueError, IndexError):
                early = False
            time.sleep(60 if early else 15 * 60)

    def _fake(self, mode):
        f = self.fake
        self.fake_cancel = False
        f.update(state="running", mode=mode, message="")
        time.sleep(1.5)
        if mode == "run":
            total = 12
            for i in range(total + 1):
                if self.fake_cancel:
                    f.update(state="failed", message="Abgebrochen. Es wurde nichts verändert.")
                    return
                f["progress"] = {"downloading": min(i * 2, total), "unpacking": max(0, i - 4),
                                 "setting_up": max(0, i - 8), "total": total}
                f["log"] = f"Get:{i} http://repo.example ...\nUnpacking paket-{i} ...\n"
                time.sleep(0.6)
            f.update(state="done", message="Update abgeschlossen.", reboot_required=True,
                     reboot_boot_id=self.boot_id(), available=0, belabox=[], packages=[])
        elif mode == "reboot":
            f.update(state="rebooting", message="Neustart wird eingeleitet. (Demo)")
        else:
            f.update(state="done", mode=mode, available=224, download="412 MB",
                     packages=["belaui", "belacoder", "belabox-linux-rk3588", "libc6"],
                     belabox=["belaui", "belacoder", "belabox-linux-rk3588"],
                     would_reboot=True, last_check=int(time.time()), held=[])
