#!/usr/bin/env python3
"""Root-Helfer: Box sauber herunterfahren oder neu starten (läuft nur über pipbox-power.path).

Liest aus der Auslösedatei ein Stichwort aus fester Liste (poweroff, reboot), löscht die Datei und führt den Befehl nach
einer kurzen Pause aus (damit die Oberfläche noch antworten kann). Nimmt nichts anderes entgegen."""
import json
import os
import subprocess
import sys
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/power-request"
RUN = "/run/pipbox-power"
CMDS = {"poweroff": ["systemctl", "poweroff"], "reboot": ["systemctl", "reboot"]}


def main():
    try:
        with open(REQ) as f:
            word = f.readline().strip()
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
