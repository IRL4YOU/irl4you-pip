#!/usr/bin/env python3
"""Root-Helfer für Systemupdates. Wird nur durch pipbox-update.path gestartet.

Liest aus der Auslösedatei ausschließlich ein Stichwort aus einer festen Liste
(check, dry, run, reboot, autocheck). Alles andere wird verworfen. Nimmt keine Befehle,
Paketnamen oder Pfade von der Weboberfläche an.

Vorgehen wie bei der Update-Funktion von belaUI: apt-get update, dann
dist-upgrade (bei zurückgehaltenen Paketen gezielt install). Neustart nötig,
wenn l4t, belabox-linux-* oder belabox-network-config dabei sind.
Nie während einer Übertragung (belacoder läuft).
"""
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/update-request"
STATUS = f"{STATE}/update-status.json"
LOG = f"{STATE}/update.log"
SNAPSHOTS = STATE
LOCK = "/run/pipbox-update.lock"
MODES = ("check", "dry", "run", "reboot", "autocheck")
REBOOT_PKGS = ("l4t", "belabox-linux-", "belabox-network-config")
MIN_FREE = 1536 * 1024 * 1024
ENV = dict(os.environ, DEBIAN_FRONTEND="noninteractive", LC_ALL="C")
APT_LOCK = ["-o", "DPkg::Lock::Timeout=300"]
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
    """Anfragedatei im Ordner des Benutzers pipbox lesen, ohne Verweisen (Symlinks) zu folgen und nur bis zur Höchstgröße."""
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
        os.unlink(tmp)                  # kein Verweis des Benutzers pipbox darf als Ziel dienen
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
    # Protokoll begrenzen
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


def run_apt(args, progress=None):
    """apt-get ausführen, Ausgabe ins Protokoll; liefert (Rückgabecode, Text)."""
    log("$ apt-get " + " ".join(args))
    p = subprocess.Popen(["apt-get"] + args, env=ENV, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, errors="replace")
    out = []
    for line in p.stdout:
        out.append(line)
        log(line)
        if progress:
            progress(line)
    return p.wait(), "".join(out)


def dpkg_interrupted():
    """Hat ein früherer Paketlauf nicht zu Ende gearbeitet (Strom weg, Neustart, abgebrochenes Update)? Dann weigert sich apt mit
    "dpkg was interrupted". Erkennbar an Resten in /var/lib/dpkg/updates oder halb eingerichteten Paketen laut dpkg --audit."""
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
    """Hält gerade ein anderer Prozess die Paketsperre (zum Beispiel eine automatische Aktualisierung)? Dann nichts anfassen."""
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
            os.close(fd)                 # das Schließen gibt die Sperre wieder frei
    return False


def repair_dpkg():
    """Einen unterbrochenen Paketlauf zu Ende führen, so wie es apt verlangt (dpkg --configure -a, danach apt-get -f install).
    Läuft gerade ein anderer Paketvorgang, geschieht nichts. True bei Erfolg."""
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
    """Verständliche Meldung für die häufigsten Fehler von apt; sonst die letzten Zeilen."""
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
    """Wie belaUI: erst dist-upgrade; wenn nichts, zurückgehaltene Pakete einzeln."""
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
    rc, _ = run_apt(["update", "--allow-releaseinfo-change"] + APT_LOCK)
    if rc != 0:
        save_status(state="failed", finished=now(),
                    message="Paketliste konnte nicht geladen werden (Internet?)")
        return
    s = plan()
    if s["rc"] != 0:
        save_status(state="failed", finished=now(), message="Planung fehlgeschlagen, siehe Protokoll")
        return
    save_status(last_check=now())
    finish_plan("check", s)


def do_autocheck(prev):
    """Tägliche stille Suche (löst der Server aus): wie "check", aber ohne den Zustand "läuft" und ohne Fehlermeldung. Läuft eine Übertragung oder
    ist die Paketliste nicht erreichbar (kein Internet), bleibt der bisherige Zustand der Karte unverändert."""
    def restore():
        save_status(**{k: prev[k] for k in ("state", "mode", "message", "finished", "started") if k in prev})
    if belacoder_running():
        return
    rc, _ = run_apt(["update", "--allow-releaseinfo-change"] + APT_LOCK)
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
    if belacoder_running():
        save_status(state="refused", finished=now(),
                    message="Übertragung läuft. Update nur ohne laufenden Stream.")
        return
    free = shutil.disk_usage("/").free
    if free < MIN_FREE:
        save_status(state="refused", finished=now(),
                    message=f"Zu wenig freier Speicher ({free // 2**20} MiB, nötig 1536 MiB).")
        return
    if dpkg_interrupted():
        if dpkg_busy():                  # die Reste gehören dann zu einem Paketvorgang, der gerade läuft
            save_status(state="failed", finished=now(), message=friendly_error("Could not get lock"))
            return
        save_status(state="running", mode="run", message="Ein früherer Paketlauf war unterbrochen, die Box schließt ihn zuerst ab …")
        if not repair_dpkg():
            save_status(state="failed", finished=now(), message=friendly_error("dpkg was interrupted"))
            return
        log("Unterbrochener Paketlauf abgeschlossen.")
        save_status(message="")
    rc, _ = run_apt(["update", "--allow-releaseinfo-change"] + APT_LOCK)
    if rc != 0:
        save_status(state="failed", finished=now(),
                    message="Paketliste konnte nicht geladen werden (Internet?)")
        return
    s = plan()
    if s["rc"] != 0 or s["count"] == 0:
        save_status(state="done", mode="run", finished=now(), available=0,
                    message="Nichts zu aktualisieren.", packages=[], belabox=[],
                    held=s["held"], would_reboot=False, reboot_required=False)
        return

    # Paketstand vor dem Update festhalten (für spätere Fehlersuche), letzte 5 behalten
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
            "-o", "Dpkg::Options::=--force-confold"] + APT_LOCK
    args += (["install"] + s["install_held"]) if s["install_held"] else ["dist-upgrade"]
    rc, out = run_apt(args, on_line)
    reboot = rc == 0 and needs_reboot(s["packages"])
    if rc == 0:
        save_status(state="done", finished=now(), progress=progress,
                    message="Update abgeschlossen.", reboot_required=reboot,
                    reboot_boot_id=boot_id(), available=0, packages=[], belabox=[])
    else:
        tail = " ".join(out.strip().splitlines()[-3:])[:300]
        save_status(state="failed", finished=now(), progress=progress, message=friendly_error(tail))


def do_reboot():
    if belacoder_running():
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
    if mode not in ("reboot", "autocheck"):
        save_status(state="running", mode=mode, started=now(), message="")
    log(f"=== {time.strftime('%F %T')} Anforderung: {mode} ===")
    try:
        {"check": do_check, "dry": do_dry, "run": do_run, "reboot": do_reboot, "autocheck": lambda: do_autocheck(prev)}[mode]()
    except Exception as e:  # nie ohne Statusmeldung enden
        log(f"Fehler: {e}")
        save_status(state="failed", finished=now(), message=f"Interner Fehler: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
