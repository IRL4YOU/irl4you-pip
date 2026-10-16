#!/usr/bin/env python3
"""Root-Helfer: Protokoll-Modus umschalten (läuft über pipbox-logmode.path oder von install.sh mit --apply).

Zwei Stufen, mehr nimmt der Helfer nicht an:
  sparsam       Journal nur im Arbeitsspeicher, Zustandsprotokoll im Arbeitsspeicher (/run). Schont die Speicherkarte,
                nach einem Absturz oder Stromausfall bleibt aber keine Spur.
  ausfuehrlich  Journal dauerhaft (höchstens 30 MB, 7 Tage), Zustandsprotokoll auf der Karte (höchstens 4 MB).
Liest aus der Auslösedatei ein Stichwort aus fester Liste, ohne Verweisen zu folgen, löscht die Datei und stellt um.

Zusätzlich (1.0.36):
  Zugriffsprotokoll von nginx: bei "sparsam" aus (kleine Datei in /etc/nginx/conf.d mit "access_log off;"), bei "ausfuehrlich" an. Das Neuladen von nginx trennt
                die Kameras etwa eine Minute; darum wird nur neu geladen, wenn gerade nicht gesendet wird (sonst gilt es ab dem nächsten Start von nginx).
  --rotate      (täglicher Timer) große Protokolle (nginx, pipbox-*.log) kopieren, komprimieren und kürzen, höchstens 3 alte Stände, nichts älter als 30 Tage.
                Das Kürzen ändert die Datei an Ort und Stelle: Es braucht kein Signal an nginx, die Kameras bleiben verbunden.
  --apply-nginx (von install.sh) das Zugriffsprotokoll passend zum gespeicherten Modus setzen."""
import fcntl
import glob
import gzip
import json
import os
import shutil
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
NGINX_DROPIN = "/etc/nginx/conf.d/pipbox-log.conf"
NGINX_TEXT = "# IRL4YOU BOX (Protokoll-Modus sparsam): kein Zugriffsprotokoll, schont die Speicherkarte. Die Datei verschwindet im Modus ausführlich.\naccess_log off;\n"
ROTATE_LOCK = "/run/pipbox-logrotate.lock"
ROTATE_KEEP = 3                           # alte Stände je Datei (.1.gz bis .3.gz)
ROTATE_MAX_AGE = 30 * 86400               # nichts Älteres bleibt liegen
ROTATE_LIMITS = (("/var/log/nginx/access.log", 20 * 1024 * 1024), ("/var/log/nginx/error.log", 5 * 1024 * 1024))
ROTATE_GLOBS = (("/var/log/pipbox-*.log", 5 * 1024 * 1024),)
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


def sending():
    """Läuft gerade eine Sendung? (belacoder ist da.)"""
    for d in glob.glob("/proc/[0-9]*"):
        try:
            with open(d + "/comm") as f:
                if f.read().strip() == "belacoder":
                    return True
        except OSError:
            pass
    return False


def apply_nginx(mode):
    """Zugriffsprotokoll von nginx zum Modus: sparsam = aus (Datei in conf.d), ausfuehrlich = an (Datei weg). Gibt einen kurzen Text zurück.
    nginx wird nur neu geladen, wenn die Prüfung klappt und gerade nicht gesendet wird (ein Neuladen trennt alle Kameras etwa eine Minute)."""
    want_off = mode == "sparsam"
    try:
        with open(NGINX_DROPIN) as f:
            is_off = f.read() == NGINX_TEXT
    except OSError:
        is_off = False
    if want_off == is_off:
        return "unverändert"
    try:
        if want_off:
            write_file(NGINX_DROPIN, NGINX_TEXT)
        else:
            os.unlink(NGINX_DROPIN)
    except OSError as e:
        return "nicht geändert (%s)" % type(e).__name__
    try:
        ok = subprocess.run(["nginx", "-t"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:                                                     # nginx lehnt es ab: alten Stand wiederherstellen
        try:
            if want_off:
                os.unlink(NGINX_DROPIN)
            else:
                write_file(NGINX_DROPIN, NGINX_TEXT)
        except OSError:
            pass
        return "nginx lehnt die Änderung ab, zurückgesetzt"
    if sending():
        return "gilt ab dem nächsten Start von nginx (es wird gerade gesendet)"
    try:
        subprocess.run(["nginx", "-s", "reload"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "geändert, Neuladen fehlgeschlagen"
    return "nginx neu geladen"


def rotate_file(path, limit, now=None):
    """Eine große Protokolldatei sichern: Inhalt komprimiert nach <Datei>.1.gz (ältere rücken nach .2.gz, .3.gz), dann an Ort und Stelle kürzen. Wer die Datei
    offen hat (nginx, ein Dienst), schreibt einfach weiter (Anhängen), es braucht kein Signal. Zeilen zwischen Kopie und Kürzen können fehlen. True, wenn gedreht wurde."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or st.st_size <= limit:
        return False
    for n in range(ROTATE_KEEP, 0, -1):
        old, new = "%s.%d.gz" % (path, n), "%s.%d.gz" % (path, n + 1)
        if n == ROTATE_KEEP:
            try:
                os.unlink(old)
            except OSError:
                pass
        elif os.path.exists(old):
            os.replace(old, new)
    tmp = path + ".1.gz.neu"
    try:
        with open(path, "rb") as src, gzip.open(tmp, "wb", compresslevel=1) as dst:
            shutil.copyfileobj(src, dst)
        os.chmod(tmp, 0o640)
        os.replace(tmp, path + ".1.gz")
        with open(path, "r+b") as f:
            f.truncate(0)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    return True


def rotate_cleanup(path, now=None):
    """Rotierte Stände, die älter als 30 Tage sind oder über die erlaubte Zahl hinausgehen, löschen."""
    now = time.time() if now is None else now
    for f in glob.glob(path + ".*.gz"):
        try:
            if now - os.lstat(f).st_mtime > ROTATE_MAX_AGE:
                os.unlink(f)
        except OSError:
            pass


def rotate_all():
    """Alle bekannten Protokolle prüfen (täglicher Timer). Gibt die Liste der gedrehten Dateien zurück."""
    lock = os.open(ROTATE_LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return []
    try:
        os.nice(19)
    except OSError:
        pass
    todo = list(ROTATE_LIMITS)
    for pat, limit in ROTATE_GLOBS:
        todo += [(p, limit) for p in sorted(glob.glob(pat)) if not p.endswith(".gz")]
    done = []
    for path, limit in todo:
        if rotate_file(path, limit):
            done.append(path)
        rotate_cleanup(path)
    return done


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
    try:
        nginx = apply_nginx(mode)
    except Exception as e:                                          # das Protokoll darf nie am Umschalten scheitern
        nginx = "Fehler (%s)" % type(e).__name__
    os.makedirs(RUN, exist_ok=True)
    write_file(f"{RUN}/status.json", json.dumps({"mode": mode, "time": int(time.time()), "nginx": nginx}))


def main(argv):
    if len(argv) == 3 and argv[1] == "--apply":          # von install.sh
        apply(argv[2])
        return 0
    if len(argv) == 2 and argv[1] == "--apply-nginx":    # von install.sh (Update): Zugriffsprotokoll zum gespeicherten Modus
        try:
            with open(MODE_FILE) as f:
                mode = f.read().strip()
        except OSError:
            return 0
        if mode in MODES:
            print("pipbox-logmode: Zugriffsprotokoll von nginx: " + apply_nginx(mode))
        return 0
    if len(argv) == 2 and argv[1] == "--rotate":          # täglicher Timer
        for p in rotate_all():
            print("pipbox-logmode: gedreht: " + p)
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
