#!/usr/bin/env python3
"""Root-Helfer: Auslöser für den Sende-Dienst (nur die Stichworte start, stop und restart)."""
import os
import subprocess
import sys

REQ = "/var/lib/pipbox/send-request"


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
