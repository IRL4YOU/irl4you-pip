#!/usr/bin/env python3
import fcntl
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/update-request"
STATUS = f"{STATE}/update-status.json"
LOG = f"{STATE}/update.log"
SNAPSHOTS = STATE
LOCK = "/run/pipbox-update.lock"
CANCEL = f"{STATE}/update-cancel"
MODES = ("check", "dry", "run", "reboot", "autocheck")
REBOOT_PKGS = ("l4t", "belabox-linux-", "belabox-network-config")
MIN_FREE = 1536 * 1024 * 1024
ENV = dict(os.environ, DEBIAN_FRONTEND="noninteractive", LC_ALL="C")
APT_LOCK = ["-o", "DPkg::Lock::Timeout=300"]
APT_NET = ["-o", "Acquire::http::Timeout=60", "-o", "Acquire::https::Timeout=60", "-o", "Acquire::Retries=3"]
LIST_LIMIT = 15 * 60
DOWNLOAD_LIMIT = 45 * 60
TIMEOUT_RC = 124
CANCEL_RC = 130
CANCEL_EV = threading.Event()
APT = {"p": None, "ok": False}
ACT = {"lines": 0, "sig": None, "t": 0.0, "mark": 0.0, "saved": 0.0}
PARTIAL_DIRS = ("/var/cache/apt/archives/partial", "/var/lib/apt/lists/partial")
MSG_CANCELLED = "Abgebrochen. Es wurde nichts verändert."
MSG_TOO_LATE = "Abbrechen ist jetzt nicht mehr möglich: Die Pakete werden eingerichtet."
DPKG_UPDATES = "/var/lib/dpkg/updates"
DPKG_LOCKS = ("/var/lib/dpkg/lock-frontend", "/var/lib/dpkg/lock")


def now():
    return int(time.time())


def boot_id():
    with open("/proc/sys/kernel/random/boot_id") as f:
        return f.read().strip()


def load_status():
    try:
        with open(STATUS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def read_req(path, limit=4096):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def save_status(**kw):
    s = load_status()
    s.update(kw)
    tmp = STATUS + ".tmp"
    try:
        os.unlink(tmp)
    except OSError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATUS)


def log(line):
    flags = os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | flags, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(line.rstrip("\n") + "\n")
    try:
        if os.path.getsize(LOG) > 512 * 1024:
            with os.fdopen(os.open(LOG, os.O_RDONLY | flags), errors="replace") as f:
                tail = f.readlines()[-2000:]
            with os.fdopen(os.open(LOG, os.O_WRONLY | os.O_TRUNC | flags), "w") as f:
                f.writelines(tail)
    except OSError:
        pass


def belacoder_running():
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/comm") as f:
                    if f.read().strip() == "belacoder":
                        return True
            except OSError:
                pass
    return False


def send_service_active():
    try:
        r = subprocess.run(["systemctl", "is-active", "pipbox-send.service"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.stdout.strip() in ("active", "activating")


def sending():
    return belacoder_running() or send_service_active()


def handle_cancel():
    try:
        os.unlink(CANCEL)
    except OSError:
        return False
    p = APT["p"]
    if p is not None and not APT["ok"]:
        log("Abbruch angefordert, aber die Pakete werden gerade eingerichtet: wird nicht abgebrochen.")
        save_status(message=MSG_TOO_LATE)
        return False
    log("Abbruch angefordert.")
    CANCEL_EV.set()
    if p is not None:
        try:
            p.kill()
        except OSError:
            pass
    return True


def partial_bytes():
    total = 0
    for d in PARTIAL_DIRS:
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def track_activity(now=None):
    if APT["p"] is None:
        return
    now = time.time() if now is None else now
    sig = (ACT["lines"], partial_bytes())
    if sig != ACT["sig"]:
        ACT["sig"], ACT["t"] = sig, now
    if ACT["t"] > ACT["mark"] and now - ACT["saved"] >= 10:
        ACT["mark"], ACT["saved"] = ACT["t"], now
        save_status(activity=int(ACT["t"]))


def watch_cancel():
    while True:
        time.sleep(1)
        try:
            handle_cancel()
            track_activity()
        except Exception as e:
            log(f"Abbruch-Wächter: {e}")


def run_apt(args, progress=None, limit=None, cancelable=True):
    if cancelable and CANCEL_EV.is_set():
        return CANCEL_RC, ""
    log("$ apt-get " + " ".join(args))
    p = subprocess.Popen(["apt-get"] + args, env=ENV, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, errors="replace")
    APT["p"], APT["ok"] = p, cancelable
    cut = []
    timer = None
    if limit:
        def stop():
            cut.append(1)
            log(f"Zeitgrenze von {int(limit // 60)} Minuten erreicht, apt-get wird beendet.")
            try:
                p.kill()
            except OSError:
                pass
        timer = threading.Timer(limit, stop)
        timer.daemon = True
        timer.start()
    out = []
    try:
        for line in p.stdout:
            ACT["lines"] += 1
            out.append(line)
            log(line)
            if progress:
                progress(line)
        rc = p.wait()
    finally:
        APT["p"] = None
        if timer:
            timer.cancel()
    if cancelable and CANCEL_EV.is_set() and rc != 0:
        rc = CANCEL_RC
    return (TIMEOUT_RC if cut and rc != CANCEL_RC else rc), "".join(out)


def list_failed_message(rc):
    if rc == TIMEOUT_RC:
        return "Zeitgrenze erreicht: Die Paketliste ließ sich nicht rechtzeitig laden (Internet?). Bitte später erneut versuchen."
    return "Paketliste konnte nicht geladen werden (Internet?)"


def dpkg_interrupted():
    try:
        if any(not n.startswith(".") for n in os.listdir(DPKG_UPDATES)):
            return True
    except OSError:
        pass
    try:
        r = subprocess.run(["dpkg", "--audit"], capture_output=True, text=True, errors="replace", timeout=60, env=ENV)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


def dpkg_busy():
    for path in DPKG_LOCKS:
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o640)
        except OSError:
            continue
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        finally:
            os.close(fd)
    return False


def repair_dpkg():
    if dpkg_busy():
        log("Es läuft ein anderer Paketvorgang, der unterbrochene Paketstand wird nicht angefasst.")
        return False
    log("$ dpkg --configure -a (unterbrochener Paketlauf)")
    try:
        p = subprocess.run(["dpkg", "--configure", "-a", "--force-confdef", "--force-confold"], env=ENV, capture_output=True,
                           text=True, errors="replace", timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"dpkg --configure -a: {e}")
        return False
    log((p.stdout or "") + (p.stderr or ""))
    if p.returncode != 0:
        return False
    rc, _ = run_apt(["-f", "install", "-y"] + APT_LOCK)
    return rc == 0


def friendly_error(tail):
    if "dpkg was interrupted" in tail:
        return ("Ein früherer Paketlauf wurde unterbrochen und ließ sich nicht selbst abschließen. Bitte in einer Konsole auf der Box "
                "ausführen: sudo dpkg --configure -a (danach: sudo apt-get -f install) und das Update dann wiederholen.")
    if "Could not get lock" in tail or "Unable to acquire the dpkg frontend lock" in tail or "Waiting for cache lock" in tail:
        return "Ein anderer Paketvorgang läuft gerade (zum Beispiel eine automatische Aktualisierung). Bitte in einigen Minuten noch einmal versuchen."
    return "Update fehlgeschlagen: " + tail


def parse_list(text, heading):
    m = re.search(re.escape(heading) + r"\n((?:  .*\n?)+)", text)
    return m.group(1).split() if m else []


def summarize(text):
    m = re.search(r"(\d+) upgraded, (\d+) newly installed", text)
    count = int(m.group(1)) + int(m.group(2)) if m else 0
    size = re.search(r"Need to get ([\d.,]+ \S+)", text)
    pk = parse_list(text, "The following packages will be upgraded:")
    new = parse_list(text, "The following NEW packages will be installed:")
    return {"count": count, "download": size.group(1) if size else "0 B",
            "packages": pk + new,
            "belabox": [x for x in pk + new if "belabox" in x or "belacoder" in x or "belaui" in x],
            "held": parse_list(text, "The following packages have been kept back:")}


def plan():
    rc, out = run_apt(["-s", "dist-upgrade"] + APT_LOCK)
    s = summarize(out)
    s["rc"] = rc
    s["install_held"] = None
    if rc == 0 and s["count"] == 0 and s["held"]:
        rc2, out2 = run_apt(["-s", "install"] + s["held"] + APT_LOCK)
        s2 = summarize(out2)
        if rc2 == 0 and s2["count"] > 0:
            s2["held"] = s["held"]
            s2["rc"] = rc2
            s2["install_held"] = s["held"]
            return s2
    return s


def needs_reboot(packages):
    return any(p.startswith(REBOOT_PKGS) for p in packages)


def finish_plan(mode, s):
    save_status(state="done", mode=mode, finished=now(), message="",
                available=s["count"], download=s["download"],
                packages=s["packages"][:80], belabox=s["belabox"],
                held=s["held"], would_reboot=needs_reboot(s["packages"]))


def do_check():
    rc, _ = run_apt(["update", "--allow-releaseinfo-change"] + APT_LOCK + APT_NET, limit=LIST_LIMIT)
    if rc != 0:
        save_status(state="failed", finished=now(), message=list_failed_message(rc))
        return
    s = plan()
    if s["rc"] != 0:
        save_status(state="failed", finished=now(), message="Planung fehlgeschlagen, siehe Protokoll")
        return
    save_status(last_check=now())
    finish_plan("check", s)


def do_autocheck(prev):
    def restore():
        save_status(**{k: prev[k] for k in ("state", "mode", "message", "finished", "started") if k in prev})
    if sending():
        return
    rc, _ = run_apt(["update", "--allow-releaseinfo-change"] + APT_LOCK + APT_NET, limit=LIST_LIMIT)
    if rc != 0:
        log("Automatische Suche: Paketliste nicht ladbar (Internet?), nichts geändert.")
        restore()
        return
    s = plan()
    if s["rc"] != 0:
        log("Automatische Suche: Planung fehlgeschlagen, nichts geändert.")
        restore()
        return
    save_status(last_check=now())
    finish_plan("check", s)


def do_dry():
    s = plan()
    if s["rc"] != 0:
        save_status(state="failed", finished=now(), message="Probelauf fehlgeschlagen, siehe Protokoll")
        return
    finish_plan("dry", s)


def do_run():
    if sending():
        save_status(state="refused", finished=now(),
                    message="Übertragung läuft. Update nur ohne laufenden Stream.")
        return
    free = shutil.disk_usage("/").free
    if free < MIN_FREE:
        save_status(state="refused", finished=now(),
                    message=f"Zu wenig freier Speicher ({free // 2**20} MiB, nötig 1536 MiB).")
        return
    if dpkg_interrupted():
        if dpkg_busy():
            save_status(state="failed", finished=now(), message=friendly_error("Could not get lock"))
            return
        save_status(state="running", mode="run", message="Ein früherer Paketlauf war unterbrochen, die Box schließt ihn zuerst ab …")
        if not repair_dpkg():
            save_status(state="failed", finished=now(), message=friendly_error("dpkg was interrupted"))
            return
        log("Unterbrochener Paketlauf abgeschlossen.")
        save_status(message="")
    rc, _ = run_apt(["update", "--allow-releaseinfo-change"] + APT_LOCK + APT_NET, limit=LIST_LIMIT)
    if rc != 0:
        save_status(state="failed", finished=now(), message=list_failed_message(rc))
        return
    s = plan()
    if s["rc"] != 0 or s["count"] == 0:
        save_status(state="done", mode="run", finished=now(), available=0,
                    message="Nichts zu aktualisieren.", packages=[], belabox=[],
                    held=s["held"], would_reboot=False, reboot_required=False)
        return

    snap = f"{SNAPSHOTS}/packages-before-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    with os.fdopen(os.open(snap, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644), "w") as f:
        f.write(subprocess.run(["dpkg-query", "-W"], capture_output=True, text=True).stdout)
    old = sorted(x for x in os.listdir(SNAPSHOTS) if x.startswith("packages-before-"))
    for x in old[:-5]:
        os.remove(f"{SNAPSHOTS}/{x}")

    total = s["count"]
    progress = {"downloading": 0, "unpacking": 0, "setting_up": 0, "total": total}
    save_status(state="running", mode="run", started=now(), message="", progress=progress,
                available=total, download=s["download"], packages=s["packages"][:80],
                belabox=s["belabox"], held=s["held"], would_reboot=needs_reboot(s["packages"]),
                snapshot=os.path.basename(snap))
    last = [0.0]

    def on_line(line):
        m = re.match(r"Get:(\d+)", line)
        if m:
            progress["downloading"] = min(max(progress["downloading"], int(m.group(1))), total)
        if line.startswith("Unpacking "):
            progress["unpacking"] = min(progress["unpacking"] + 1, total)
        if line.startswith("Setting up "):
            progress["setting_up"] = min(progress["setting_up"] + 1, total)
        if time.time() - last[0] > 2:
            last[0] = time.time()
            save_status(progress=progress)

    args = ["-y", "-o", "Dpkg::Options::=--force-confdef",
            "-o", "Dpkg::Options::=--force-confold"] + APT_LOCK + APT_NET
    args += (["install"] + s["install_held"]) if s["install_held"] else ["dist-upgrade"]
    rc, out = run_apt(["--download-only"] + args, on_line, limit=DOWNLOAD_LIMIT)
    if rc != 0:
        if rc == TIMEOUT_RC:
            msg = "Zeitgrenze erreicht: Die Pakete ließen sich nicht rechtzeitig laden (Internet?). Es wurde nichts verändert, bitte später erneut versuchen."
        else:
            msg = friendly_error(" ".join(out.strip().splitlines()[-3:])[:300])
        save_status(state="failed", finished=now(), progress=progress, message=msg)
        return
    progress["downloading"] = total
    save_status(progress=progress)
    if CANCEL_EV.is_set():
        save_status(state="failed", finished=now(), progress=progress, message=MSG_CANCELLED)
        return
    save_status(installing=True)
    rc, out = run_apt(args, on_line, cancelable=False)
    reboot = rc == 0 and needs_reboot(s["packages"])
    if rc == 0:
        save_status(state="done", finished=now(), progress=progress,
                    message="Update abgeschlossen.", reboot_required=reboot,
                    reboot_boot_id=boot_id(), available=0, packages=[], belabox=[])
    else:
        tail = " ".join(out.strip().splitlines()[-3:])[:300]
        save_status(state="failed", finished=now(), progress=progress, message=friendly_error(tail))


def do_reboot():
    if sending():
        save_status(state="refused", finished=now(),
                    message="Übertragung läuft. Neustart nur ohne laufenden Stream.")
        return
    save_status(state="rebooting", finished=now(), message="Neustart wird eingeleitet.")
    log("Neustart auf Anforderung.")
    time.sleep(3)
    subprocess.run(["systemctl", "reboot"])


def main():
    try:
        mode = (read_req(REQ).splitlines() or [""])[0].strip()
    except OSError:
        return 0
    try:
        os.remove(REQ)
    except OSError:
        pass
    if mode not in MODES:
        log("Unbekannte Anforderung verworfen.")
        return 0
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("Es läuft bereits ein Update; Anforderung verworfen.")
        return 0
    prev = load_status()

    def on_term(signum, frame):
        if mode not in ("reboot", "autocheck"):
            save_status(state="failed", finished=now(), message="Das Update wurde vom System beendet (Zeitgrenze). Bitte später erneut versuchen.")
        sys.exit(1)
    signal.signal(signal.SIGTERM, on_term)
    try:
        os.unlink(CANCEL)
    except OSError:
        pass
    CANCEL_EV.clear()
    if mode != "reboot":
        threading.Thread(target=watch_cancel, daemon=True).start()
    if mode not in ("reboot", "autocheck"):
        save_status(state="running", mode=mode, started=now(), message="", installing=False, activity=now())
    log(f"=== {time.strftime('%F %T')} Anforderung: {mode} ===")
    try:
        {"check": do_check, "dry": do_dry, "run": do_run, "reboot": do_reboot, "autocheck": lambda: do_autocheck(prev)}[mode]()
        if CANCEL_EV.is_set() and mode not in ("reboot", "autocheck") and load_status().get("state") == "failed":
            save_status(message=MSG_CANCELLED)
    except Exception as e:
        log(f"Fehler: {e}")
        save_status(state="failed", finished=now(), message=f"Interner Fehler: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
