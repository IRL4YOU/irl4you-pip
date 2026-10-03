#!/usr/bin/env python3
"""Root-Helfer: Protokoll-Modus umschalten (läuft über pipbox-logmode.path oder von install.sh mit --apply).

Zwei Stufen, mehr nimmt der Helfer nicht an:
  sparsam       Journal nur im Arbeitsspeicher, Zustandsprotokoll im Arbeitsspeicher (/run). Schont die Speicherkarte,
                nach einem Absturz oder Stromausfall bleibt aber keine Spur.
  ausfuehrlich  Journal dauerhaft (höchstens 30 MB, 7 Tage), Zustandsprotokoll auf der Karte (höchstens 4 MB).
Liest aus der Auslösedatei ein Stichwort aus fester Liste, ohne Verweisen zu folgen, löscht die Datei und stellt um."""
import fcntl
import json
import os
import stat
import subprocess
import sys
import time

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/logmode-request"
RUN = "/run/pipbox-logmode"
CONF_DIR = "/etc/pipbox"
MODE_FILE = f"{CONF_DIR}/logmode"
JOURNAL_DIR = "/etc/systemd/journald.conf.d"
JOURNAL_CONF = f"{JOURNAL_DIR}/pipbox-journal.conf"
OLD_JOURNAL_CONF = f"{JOURNAL_DIR}/pipbox-persistent.conf"        # Name bis 0.9.12
LOCK = "/run/pipbox-logmode.lock"
JOURNAL_LOG_DIR = "/var/log/journal"
MODES = ("sparsam", "ausfuehrlich")
JOURNAL_TEXT = {
    "sparsam": "[Journal]\nStorage=volatile\nRuntimeMaxUse=20M\n",
    "ausfuehrlich": "[Journal]\nStorage=persistent\nSystemMaxUse=30M\nSystemMaxFileSize=3M\nMaxRetentionSec=7day\nSyncIntervalSec=5min\n",
}


def read_req(path, limit=64):
    """Anfragedatei im Ordner des Benutzers pipbox lesen, ohne Verweisen (Symlinks) zu folgen und nur bis zur Höchstgröße."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def write_file(path, text):
    """Datei als root atomar schreiben (Ordner gehören root)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        os.unlink(tmp)
    except OSError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def apply(mode):
    if mode not in MODES:
        raise ValueError("unbekannter Modus")
    os.makedirs(CONF_DIR, exist_ok=True)
    write_file(MODE_FILE, mode + "\n")
    write_file(JOURNAL_CONF, JOURNAL_TEXT[mode])
    try:
        os.unlink(OLD_JOURNAL_CONF)
    except OSError:
        pass
    if mode == "ausfuehrlich":
        os.makedirs(JOURNAL_LOG_DIR, exist_ok=True)
    subprocess.run(["systemctl", "restart", "systemd-journald"], capture_output=True)
    if mode == "ausfuehrlich":
        subprocess.run(["journalctl", "--flush"], capture_output=True)
    os.makedirs(RUN, exist_ok=True)
    write_file(f"{RUN}/status.json", json.dumps({"mode": mode, "time": int(time.time())}))


def main(argv):
    if len(argv) == 3 and argv[1] == "--apply":          # von install.sh
        apply(argv[2])
        return 0
    try:
        word = (read_req(REQ).splitlines() or [""])[0].strip()
    except OSError:
        return 0
    try:
        os.remove(REQ)
    except OSError:
        pass
    if word not in MODES:
        return 1
    lock = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    apply(word)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
