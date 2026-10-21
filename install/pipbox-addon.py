#!/usr/bin/env python3
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time

STATE = os.environ.get("PB_ADDON_STATE", "/var/lib/pipbox")
REQ = f"{STATE}/addon-request"
RUN = os.environ.get("PB_ADDON_RUN", "/run/pipbox-addon")
STATUS = f"{RUN}/status.json"
LOCK = os.environ.get("PB_ADDON_LOCK", "/run/pipbox-addon.lock")
INSTALL = os.environ.get("PB_ADDON_INSTALL", "/opt/pipbox")
SRC = f"{INSTALL}/addons/datenschutz"
HOME = f"{INSTALL}/privacy"
UNIT = "/etc/systemd/system/pipbox-privacy.service"
SUP = f"{INSTALL}/pipbox-privacy.py"
CONFIG = f"{STATE}/datenschutz.json"
ACTIONS = ("datenschutz-install", "datenschutz-remove")
TIMEOUT = 2400
STEPS = {
    "programme": (8, "Kopiere die Programme"),
    "modelle": (12, "Lade die Modelle der Erkennung (einige MB)"),
    "npu": (25, "Richte die NPU-Laufzeit ein"),
    "umgebung": (35, "Richte die Python-Umgebung ein (das dauert einige Minuten)"),
    "dienst": (92, "Starte den Dienst"),
}


def status(**kw):
    os.makedirs(RUN, exist_ok=True)
    try:
        with open(STATUS) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.update(kw)
    s["time"] = int(time.time())
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATUS)


def read_req(path, limit=64):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def sending():
    try:
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    with open(f"/proc/{d}/comm") as f:
                        if f.read().strip() == "belacoder":
                            return True
                except OSError:
                    pass
        r = subprocess.run(["systemctl", "is-active", "pipbox-send.service"], capture_output=True, text=True)
        return r.stdout.strip() in ("active", "activating")
    except OSError:
        return False


def installed():
    return os.path.exists(f"{HOME}/venv/bin/python") and os.path.exists(f"{HOME}/modelle/kopf_fp16.rknn") and os.path.exists(UNIT) and os.path.exists(f"{INSTALL}/gst/libgstpbpixel.so")


def run_script():
    script = f"{SRC}/install/install-datenschutz.sh"
    p = subprocess.Popen(["sh", script, SRC], cwd=SRC, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True)
    killed = []

    def too_long():
        killed.append(1)
        try:
            os.killpg(p.pid, 9)
        except OSError:
            p.kill()
    timer = threading.Timer(TIMEOUT, too_long)
    timer.start()
    tail = []
    try:
        for line in p.stdout:
            line = line.rstrip("\n")
            m = re.match(r"^PIPBOX-ADDON (\w+)$", line)
            if m and m.group(1) in STEPS:
                pct, txt = STEPS[m.group(1)]
                status(step=txt, progress=pct)
                continue
            tail.append(line)
            del tail[:-25]
        rc = p.wait()
    finally:
        timer.cancel()
    return (124 if killed else rc), tail


def do_install():
    if not os.path.isfile(f"{SRC}/install/install-datenschutz.sh"):
        raise RuntimeError("Die Add-on-Dateien fehlen (bitte ein Software-Update ausführen)")
    status(state="installing", action="install", step="Starte die Einrichtung", message="", progress=2)
    rc, tail = run_script()
    sys.stderr.write("install-datenschutz.sh Ausgabe (Ende):\n" + "\n".join(tail) + "\n")
    if rc != 0 or not installed():
        hint = next((l for l in reversed(tail) if l.startswith("WARNUNG")), "")
        raise RuntimeError((hint or "Die Einrichtung ist nicht vollständig durchgelaufen") + (" (Zeitgrenze erreicht)" if rc == 124 else ""))
    status(state="done", action="install", step="", message="Die Erkennung ist eingerichtet.", progress=100)


def switch_off_in_config():
    try:
        with open(CONFIG) as f:
            c = json.load(f)
        if isinstance(c, dict) and c.get("an"):
            c["an"] = False
            tmp = CONFIG + ".tmp"
            with open(tmp, "w") as f:
                json.dump(c, f, indent=1, ensure_ascii=False)
            os.chmod(tmp, 0o644)
            shutil.copymode(CONFIG, tmp)
            os.replace(tmp, CONFIG)
            try:
                shutil.chown(CONFIG, "pipbox", "pipbox")
            except (OSError, LookupError):
                pass
    except (OSError, ValueError):
        pass


def do_remove():
    status(state="installing", action="remove", step="Beende den Dienst", message="", progress=10)
    switch_off_in_config()
    subprocess.run(["systemctl", "disable", "--now", "pipbox-privacy.service"], capture_output=True)
    status(step="Entferne Programme, Modelle und Python-Umgebung", progress=50)
    for f in (UNIT, SUP):
        try:
            os.remove(f)
        except OSError:
            pass
    shutil.rmtree(HOME, ignore_errors=True)
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    status(state="done", action="remove", step="", message="Die Erkennung ist entfernt.", progress=100)


def main():
    os.makedirs(RUN, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    try:
        word = read_req(REQ).strip()
    except OSError:
        word = ""
    try:
        os.remove(REQ)
    except OSError:
        pass
    if word not in ACTIONS:
        sys.stderr.write("Unbekannte Anforderung verworfen.\n")
        return 0
    if sending():
        status(state="refused", action=word.split("-")[1], step="", message="Es wird gerade gesendet. Bitte zuerst die Übertragung beenden.", progress=0)
        return 0
    try:
        if word == "datenschutz-install":
            do_install()
        else:
            do_remove()
    except Exception as e:
        sys.stderr.write(f"Fehler: {e!r}\n")
        status(state="failed", action=word.split("-")[1], step="", message=str(e)[:160], progress=0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
