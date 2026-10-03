#!/usr/bin/env python3
"""Root-Helfer für Systemupdates. Wird nur durch pipbox-update.path gestartet.

Liest aus der Auslösedatei ausschließlich ein Stichwort aus einer festen Liste
(check, dry, run, reboot). Alles andere wird verworfen. Nimmt keine Befehle,
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
import subprocess
import sys
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/update-request"
STATUS = f"{STATE}/update-status.json"
LOG = f"{STATE}/update.log"
SNAPSHOTS = STATE
LOCK = "/run/pipbox-update.lock"
MODES = ("check", "dry", "run", "reboot")
REBOOT_PKGS = ("l4t", "belabox-linux-", "belabox-network-config")
MIN_FREE = 1536 * 1024 * 1024
ENV = dict(os.environ, DEBIAN_FRONTEND="noninteractive", LC_ALL="C")
APT_LOCK = ["-o", "DPkg::Lock::Timeout=300"]


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


def save_status(**kw):
    s = load_status()
    s.update(kw)
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATUS)


def log(line):
    with open(LOG, "a") as f:
        f.write(line.rstrip("\n") + "\n")
    # Protokoll begrenzen
    try:
        if os.path.getsize(LOG) > 512 * 1024:
            with open(LOG) as f:
                tail = f.readlines()[-2000:]
            with open(LOG, "w") as f:
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
    with open(snap, "w") as f:
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
        save_status(state="failed", finished=now(), progress=progress,
                    message="Update fehlgeschlagen: " + tail)


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
        with open(REQ) as f:
            mode = f.readline().strip()
    except OSError:
        return 0
    try:
        os.remove(REQ)
    except OSError:
        pass
    if mode not in MODES:
        log(f"Unbekannte Anforderung verworfen: {mode[:20]!r}")
        return 0
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("Es läuft bereits ein Update; Anforderung verworfen.")
        return 0
    if mode != "reboot":
        save_status(state="running", mode=mode, started=now(), message="")
    log(f"=== {time.strftime('%F %T')} Anforderung: {mode} ===")
    try:
        {"check": do_check, "dry": do_dry, "run": do_run, "reboot": do_reboot}[mode]()
    except Exception as e:  # nie ohne Statusmeldung enden
        log(f"Fehler: {e}")
        save_status(state="failed", finished=now(), message=f"Interner Fehler: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
