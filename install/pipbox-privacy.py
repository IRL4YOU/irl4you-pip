#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
import time

CFG = os.environ.get("PB_PIPELINE", "/var/lib/pipbox/pipeline.json")
HOME = "/opt/pipbox/privacy"
PY = HOME + "/venv/bin/python"
APP = HOME + "/pipbox_privacy.py"
PORT = "8790"

proc = None
fails = 0
next_try = 0.0


def wanted():
    try:
        with open(CFG) as f:
            c = json.load(f)
    except (OSError, ValueError):
        return None
    if c.get("privacy") is not True or c.get("type") == "pip":
        return False
    try:
        return max(2.0, min(12.0, float(c.get("privacy_delay_ms", 6000)) / 1000.0))
    except (TypeError, ValueError):
        return 6.0


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


def main():
    global proc, fails, next_try
    signal.signal(signal.SIGTERM, lambda *_: (stop(), sys.exit(0)))
    started = 0.0
    while True:
        w = wanted()
        if w is None:
            time.sleep(2)
            continue
        if proc and proc.poll() is not None:
            fails = fails + 1 if time.time() - started < 60 else 0
            next_try = time.time() + min(60, 3 * (fails + 1))
            print("Dienst beendet (Code %s), Neustart in %d s" % (proc.returncode, next_try - time.time()), flush=True)
            proc = None
        if w is False:
            if proc:
                print("Datenschutz aus: Dienst wird beendet", flush=True)
                stop()
            fails = 0
        elif not proc and time.time() >= next_try and os.path.exists(PY) and os.path.exists(APP):
            env = dict(os.environ, PB_NO_GST="1", PB_BIND="127.0.0.1", MARGIN_FACE="0.30", MARGIN_PX="10", TAP_FPS="10")
            proc = subprocess.Popen([PY, "-u", APP, "-", "%g" % w, PORT], cwd=HOME, env=env)
            started = time.time()
            print("Datenschutz an: Dienst gestartet (Verzögerung %g s)" % w, flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
