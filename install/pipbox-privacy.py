#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
import time

CFG = os.environ.get("PB_DATENSCHUTZ", "/var/lib/pipbox/datenschutz.json")
HOME = "/opt/pipbox/privacy"
PY = HOME + "/venv/bin/python"
APP = HOME + ("/pipbox_privacy.pyc" if os.path.exists(HOME + "/pipbox_privacy.pyc") else "/pipbox_privacy.py")
PORT = "8790"
PIPELINE = "/var/tmp/pipbox/pipeline"

proc = None
fails = 0
next_try = 0.0
laufend = None


def lesen():
    try:
        with open(CFG) as f:
            c = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(c, dict) or c.get("an") is not True or c.get("lizenz_ok") is not True:
        return False
    try:
        delay = max(2.0, min(12.0, float(c.get("delay_ms", 2000)) / 1000.0))
    except (TypeError, ValueError):
        delay = 2.0
    zonen = []
    for z in c.get("zonen", []) if isinstance(c.get("zonen"), list) else []:
        try:
            z = [float(v) for v in z]
        except (TypeError, ValueError):
            continue
        if len(z) == 4 and all(0.0 <= v <= 1.0 for v in z) and z[0] < z[2] and z[1] < z[3]:
            zonen.append(z)
    return {"delay": delay, "kopf": c.get("kopf", True) is not False, "kennz": c.get("kennz", True) is not False,
            "zweit": c.get("zweit", True) is not False, "zonen": zonen[:16]}


def maske_in_betrieb():
    try:
        with open(PIPELINE) as f:
            if "pbpixelmask" not in f.read():
                return False
    except OSError:
        return False
    return subprocess.run(["pgrep", "-x", "belacoder"], capture_output=True).returncode == 0


def stop():
    global proc
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    proc = None


def start(w):
    env = dict(os.environ, PB_NO_GST="1", PB_BIND="127.0.0.1", MARGIN_FACE="0.30", MARGIN_PX="10", TAP_FPS="10",
               PB_KOPF="1" if w["kopf"] else "0", PB_KENN="1" if w["kennz"] else "0", PB_ZWEIT="1" if w["zweit"] else "0",
               OWN_ZONES=json.dumps(w["zonen"]))
    return subprocess.Popen([PY, "-u", APP, "-", "%g" % w["delay"], PORT], cwd=HOME, env=env)


def main():
    global proc, fails, next_try, laufend
    signal.signal(signal.SIGTERM, lambda *_: (stop(), sys.exit(0)))
    started = 0.0
    while True:
        w = lesen()
        if w is None:
            time.sleep(2)
            continue
        if proc and proc.poll() is not None:
            fails = fails + 1 if time.time() - started < 60 else 0
            next_try = time.time() + min(60, 3 * (fails + 1))
            print("Dienst beendet (Code %s), Neustart in %d s" % (proc.returncode, next_try - time.time()), flush=True)
            proc = None
        if w is False:
            if proc and maske_in_betrieb():
                pass
            elif proc:
                print("Datenschutz aus: Dienst wird beendet", flush=True)
                stop()
            fails = 0
        elif proc and w != laufend:
            if maske_in_betrieb() and w["delay"] == laufend["delay"]:
                laufend = w
            elif maske_in_betrieb():
                pass
            else:
                print("Einstellung geändert: Dienst wird neu gestartet", flush=True)
                stop()
                next_try = 0.0
        elif not proc and time.time() >= next_try and os.path.exists(PY) and os.path.exists(APP):
            proc = start(w)
            laufend = w
            started = time.time()
            print("Datenschutz an: Dienst gestartet (Verzögerung %g s, Köpfe %s, Kennzeichen %s, Prüfstufe %s, Zonen %d)"
                  % (w["delay"], w["kopf"], w["kennz"], w["zweit"], len(w["zonen"])), flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
