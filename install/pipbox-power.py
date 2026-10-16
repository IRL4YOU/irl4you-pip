#!/usr/bin/env python3
"""Root-Helfer: Box sauber herunterfahren oder neu starten (läuft nur über pipbox-power.path).

Liest aus der Auslösedatei ein Stichwort aus fester Liste (poweroff, reboot), löscht die Datei und führt den Befehl nach
einer kurzen Pause aus (damit die Oberfläche noch antworten kann). Nimmt nichts anderes entgegen."""
import json
import os
import stat
import subprocess
import sys
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/power-request"
RUN = "/run/pipbox-power"
CMDS = {"poweroff": ["systemctl", "poweroff"], "reboot": ["systemctl", "reboot"]}


def read_req(path, limit=4096):
    """Anfragedatei im Ordner des Benutzers pipbox lesen, ohne Verweisen (Symlinks) zu folgen und nur bis zur Höchstgröße."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def main():
    try:
        word = (read_req(REQ, 64).splitlines() or [""])[0].strip()
    except OSError:
        word = ""
    try:
        os.remove(REQ)
    except OSError:
        pass
    if word not in CMDS:
        return 1
    os.makedirs(RUN, exist_ok=True)
    with open(f"{RUN}/status.json", "w") as f:
        json.dump({"action": word, "time": int(time.time())}, f)
    time.sleep(3)
    subprocess.run(["sync"])
    subprocess.run(["journalctl", "--rotate"], capture_output=True)   # Protokoll sauber abschließen (hilft nach einem Ausfall)
    subprocess.run(["sync"])
    subprocess.run(CMDS[word])
    return 0


if __name__ == "__main__":
    sys.exit(main())
