#!/usr/bin/env python3
import fcntl
import json
import os
import subprocess
import sys

REQ = "/var/lib/pipbox/send-request"
UPDATE_STATUS = "/var/lib/pipbox/update-status.json"
UPDATE_LOCK = "/run/pipbox-update.lock"


def update_installing():
    try:
        with open(UPDATE_STATUS) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(st, dict) or st.get("state") != "running" or st.get("mode") != "run":
        return False
    try:
        fd = os.open(UPDATE_LOCK, os.O_RDONLY)
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
    if mode == "start":
        if update_installing():
            print("send-ctl: Start verweigert, ein Systemupdate wird gerade eingespielt", flush=True)
            return 0
        subprocess.run(["systemctl", "start", "--no-block", "pipbox-send.service"])
    elif mode == "restart":
        subprocess.run(["systemctl", "restart", "--no-block", "pipbox-send.service"])
    elif mode == "stop":
        subprocess.run(["systemctl", "stop", "pipbox-send.service"])
    else:
        print(f"send-ctl: unbekannte Anforderung verworfen: {mode[:20]!r}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
