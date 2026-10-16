#!/usr/bin/env python3
"""Root-Helfer für das Software-Update von IRL4YOU BOX (läuft nur über pipbox-swupdate.path).

Liest aus der Auslösedatei ausschließlich ein Stichwort aus einer festen Liste (install, rollback, switch) und bei
"switch" eine Versionsnummer, die streng geprüft wird (nur Ziffern nach Muster x.y.z). Nimmt keine Adressen, Pfade oder
Befehle von der Weboberfläche an. Die Quelle ist fest im Programm eingetragen.

Ablauf bei "install":
  1. nicht während einer Übertragung,
  2. Archiv von der festen GitHub-Adresse laden (nur HTTPS, Größe begrenzt),
  3. Archiv streng prüfen und in ein privates Verzeichnis entpacken (nur normale Dateien, keine Verweise,
     keine Pfade nach außen),
  4. Inhalt prüfen (Pflichtdateien, Versionsnummer neuer, Python-Dateien fehlerfrei, install.sh fehlerfrei),
  5. alte Version sichern,
  6. install.sh aus dem entpackten Archiv ausführen,
  7. prüfen, ob die Oberfläche wieder läuft, sonst automatisch zurückrollen.
"Rollback" stellt die zuletzt gesicherte Version wieder her. "Switch <Version>" wechselt gezielt auf eine Version, die
entweder lokal gesichert ist (die letzten 5 bleiben liegen) oder als Release-Marke v<Version> auf GitHub liegt; das darf
auch eine ältere Version sein. Jeder Wechsel sichert vorher die jetzige Version, sodass man zurückgehen kann.

Hinweis zum Vertrauen: Wie bei jedem Update wird Code aus dem Repository als root ausgeführt. Die Quelle ist daher
fest auf das eigene Repository gesetzt; ein Update lässt sich nur auslösen, wenn dort eine NEUERE Version liegt.
"""
import fcntl
import io
import json
import os
import stat
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request

STATE = "/var/lib/pipbox"
REQ = f"{STATE}/swupdate-request"
RUN = "/run/pipbox-swupdate"
STATUS = f"{RUN}/status.json"
LOG = "/var/log/pipbox-swupdate.log"
BACKUP = "/var/lib/pipbox-backup"          # nur für root, getrennt vom Zustand des Benutzers pipbox
INSTALL = "/opt/pipbox"
UNIT_DIR = "/etc/systemd/system"
LOCK = "/run/pipbox-swupdate.lock"
URL = "https://codeload.github.com/IRL4YOU/irl4you-pip/tar.gz/refs/heads/main"
TAG_URL = "https://codeload.github.com/IRL4YOU/irl4you-pip/tar.gz/refs/tags/v{}"
MODES = ("install", "rollback", "switch")
KEEP = 5                                    # so viele gesicherte Versionen bleiben auf der Box
MAX_BYTES = 8 * 1024 * 1024
MAX_FILES = 300
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
REQUIRED = ("VERSION", "server.py", "dji.py", "dji_daemon.py", "phone_battery.py", "controllers.py", "controller_keys.py", "hdmi_daemon.py", "install/pipbox-hdmi.service", "pipbox_send.py", "pipbox_send_ctl.py",
            "web/index.html", "web/login.html", "web/i18n.js", "web/i18n/languages.json", "install/install.sh", "gst/gstpbpip.c", "gst/build.sh")
VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}(-[a-z0-9.]{1,16})?$")


class Refuse(Exception):
    pass


def log(msg):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 200_000:        # Protokoll klein halten (letzte ~100 KB bleiben)
            with open(LOG, "rb") as f:
                f.seek(-100_000, 2)
                f.readline()
                keep = f.read()
            with open(LOG, "wb") as f:
                f.write(keep)
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass
    print(msg, flush=True)


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


def read_req(path, limit=4096):
    """Anfragedatei im Ordner des Benutzers pipbox lesen, ohne Verweisen (Symlinks) zu folgen und nur bis zur Höchstgröße."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("keine normale Datei")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def vkey(v):
    """Sortierschlüssel: Zahlen, dann Release vor Vorabversion."""
    m = VERSION_RE.match(v)
    if not m:
        raise Refuse(f"Ungültige Versionsnummer: {v[:20]!r}")
    core, _, pre = v.partition("-")
    nums = tuple(int(x) for x in core.split("."))
    return nums, (1, ()) if not pre else (0, tuple(pre.split(".")))


def local_version():
    return read(f"{INSTALL}/VERSION", "0.0.0")


def sending():
    for d in os.listdir("/proc"):
        if d.isdigit() and read(f"/proc/{d}/comm") == "belacoder":
            return True
    r = subprocess.run(["systemctl", "is-active", "pipbox-send.service"], capture_output=True, text=True)
    return r.stdout.strip() in ("active", "activating")


TEST_TARBALL = None      # nur für Tests auf der Kommandozeile als root (--tarball DATEI), nie über die Weboberfläche
IGNORE_SENDING = False   # ebenso nur per Kommandozeile (--ignore-sending); die Weboberfläche kann das nie setzen


def download(url=URL, progress=None):
    """Archiv laden. progress(Prozent 0-100) wird beim Laden aufgerufen, wenn die Größe bekannt ist."""
    if TEST_TARBALL:
        with open(TEST_TARBALL, "rb") as f:
            return f.read(MAX_BYTES + 1)
    req = urllib.request.Request(url, headers={"User-Agent": "irl4you-box-update"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if not r.geturl().startswith("https://"):
            raise Refuse("Die Quelle hat nicht auf HTTPS geantwortet")
        try:
            total = int(r.headers.get("Content-Length") or 0)
        except ValueError:
            total = 0
        chunks, got, last = [], 0, 0.0
        while got <= MAX_BYTES:
            part = r.read(65536)
            if not part:
                break
            chunks.append(part)
            got += len(part)
            if progress and total and time.time() - last >= 0.5:
                last = time.time()
                progress(min(100, got * 100 // total))
        data = b"".join(chunks)
    if len(data) > MAX_BYTES:
        raise Refuse("Das Archiv ist größer als erlaubt")
    return data


def safe_extract(data, dest):
    """Entpackt nur normale Dateien und Ordner. Verweise, Geräte und Pfade nach außen werden abgelehnt."""
    os.makedirs(dest, mode=0o700)
    n = total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar:
            parts = m.name.split("/")[1:]                       # oberster Ordner (irl4you-pip-main) entfällt
            if not parts or parts == [""]:
                continue
            rel = posixpath.normpath("/".join(parts))
            if rel.startswith("/") or rel == ".." or rel.startswith("../") or "\x00" in rel:
                raise Refuse(f"Unzulässiger Pfad im Archiv: {m.name[:60]!r}")
            if rel.startswith(".git"):
                continue
            if m.isdir():
                os.makedirs(os.path.join(dest, rel), mode=0o755, exist_ok=True)
                continue
            if not m.isfile():
                raise Refuse(f"Unzulässiger Eintrag im Archiv (Verweis oder Gerät): {m.name[:60]!r}")
            n += 1
            total += m.size
            if n > MAX_FILES or m.size > MAX_FILE or total > MAX_TOTAL:
                raise Refuse("Das Archiv enthält zu viele oder zu große Dateien")
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
            with tar.extractfile(m) as src, open(target, "xb") as out:
                out.write(src.read(MAX_FILE + 1))
            os.chmod(target, 0o755 if m.mode & 0o111 else 0o644)
    return n


def validate(tree, local, wanted=None):
    """wanted=None: das Archiv muss NEUER sein. wanted=Version: das Archiv muss genau diese Version sein (auch älter)."""
    for f in REQUIRED:
        if not os.path.isfile(os.path.join(tree, f)):
            raise Refuse(f"Pflichtdatei fehlt im Archiv: {f}")
    for page in ("web/index.html", "web/login.html"):               # eine Fehlerseite statt der Oberfläche (beim Hochladen passiert) wird nie eingespielt
        text = read(f"{tree}/{page}") or ""
        if not text.lstrip().lower().startswith("<!doctype html") or "IRL4YOU" not in text or "</html>" not in text.lower():
            raise Refuse(f"Die Seite im Archiv ist beschädigt: {page}")
    new = read(f"{tree}/VERSION")
    if wanted is None:
        if vkey(new) <= vkey(local):
            raise Refuse(f"Keine neuere Version (installiert {local}, Archiv {new})")
    elif new != wanted:
        raise Refuse(f"Das Archiv enthält Version {new}, nicht die gewünschte {wanted}")
    for root, _, files in os.walk(tree):
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                try:
                    compile(open(p, encoding="utf-8").read(), p, "exec")
                except (SyntaxError, ValueError, UnicodeDecodeError) as e:
                    raise Refuse(f"Python-Fehler in {os.path.relpath(p, tree)}: {str(e)[:60]}")
    r = subprocess.run(["sh", "-n", f"{tree}/install/install.sh"], capture_output=True, text=True)
    if r.returncode != 0:
        raise Refuse("install.sh im Archiv ist fehlerhaft")
    return new


def bdir(v):
    return f"{BACKUP}/{v}"


def migrate_legacy():
    """Frühere Ablage (eine Version direkt in BACKUP) in ein Unterverzeichnis je Version überführen."""
    if os.path.isdir(f"{BACKUP}/opt-pipbox") and os.path.isfile(f"{BACKUP}/VERSION"):
        v = read(f"{BACKUP}/VERSION")
        if VERSION_RE.match(v) and not os.path.isdir(bdir(v)):
            os.makedirs(bdir(v), mode=0o755)
            for n in ("opt-pipbox", "units", "VERSION"):
                if os.path.exists(f"{BACKUP}/{n}"):
                    shutil.move(f"{BACKUP}/{n}", f"{bdir(v)}/{n}")


def list_backups():
    """Gesicherte Versionen, neueste zuerst."""
    migrate_legacy()
    out = []
    try:
        for n in os.listdir(BACKUP):
            if VERSION_RE.match(n) and os.path.isdir(f"{bdir(n)}/opt-pipbox"):
                out.append((os.stat(bdir(n)).st_mtime, n))
    except OSError:
        pass
    return [n for _, n in sorted(out, reverse=True)]


def prune(keep=KEEP):
    for n in list_backups()[keep:]:
        shutil.rmtree(bdir(n), ignore_errors=True)


def backup(local, do_prune=True):
    os.makedirs(BACKUP, mode=0o755, exist_ok=True)
    os.chmod(BACKUP, 0o755)                      # nur Programmdateien (öffentlicher Quelltext), keine Zugangsdaten
    migrate_legacy()
    shutil.rmtree(bdir(local), ignore_errors=True)
    os.makedirs(f"{bdir(local)}/units", mode=0o755)
    shutil.copytree(INSTALL, f"{bdir(local)}/opt-pipbox", symlinks=True)
    for f in os.listdir(UNIT_DIR):
        if f.startswith("pipbox") and f.endswith((".service", ".path")):
            shutil.copy2(os.path.join(UNIT_DIR, f), f"{bdir(local)}/units/{f}")
    with open(f"{bdir(local)}/VERSION", "w") as f:
        f.write(local + "\n")
    if do_prune:
        prune()


def files_differ(a, b, names):
    for n in names:
        try:
            if open(os.path.join(a, n), "rb").read() != open(os.path.join(b, n), "rb").read():
                return True
        except OSError:
            return True
    return False


def wait_active(unit, seconds=40):
    end = time.time() + seconds
    while time.time() < end:
        if subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0:
            return True
        time.sleep(2)
    return False


def restore(ver, reason="", note="Zurück auf"):
    """Stellt eine gesicherte Version wieder her. Die jetzige Version wird vorher gesichert (rückgängig machbar)."""
    src = bdir(ver)
    if not os.path.isdir(f"{src}/opt-pipbox"):
        raise Refuse(f"Version {ver} ist nicht gesichert")
    local = local_version()
    status(state="installing", step=f"Stelle Version {ver} wieder her", message="", frm=local, to=ver, progress=10)
    dji_names = ("dji.py", "dji_daemon.py", "phone_battery.py", "controllers.py")
    dji_changed = files_differ(f"{src}/opt-pipbox", INSTALL, dji_names)
    hdmi_changed = files_differ(f"{src}/opt-pipbox", INSTALL, ("hdmi_daemon.py",))
    if local != ver and VERSION_RE.match(local):
        backup(local, do_prune=False)            # erst sichern, dann (unten) aufräumen: das Ziel darf nicht verschwinden
    for name in os.listdir(INSTALL):             # gebautes Plugin bleibt, Programme werden ersetzt
        if name in ("gst",):
            continue
        p = os.path.join(INSTALL, name)
        shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
    for name in os.listdir(f"{src}/opt-pipbox"):
        if name == "gst":
            continue
        s = os.path.join(f"{src}/opt-pipbox", name)
        d = os.path.join(INSTALL, name)
        shutil.copytree(s, d, symlinks=True) if os.path.isdir(s) else shutil.copy2(s, d)
    for f in os.listdir(f"{src}/units"):
        shutil.copy2(f"{src}/units/{f}", os.path.join(UNIT_DIR, f))
    status(progress=60)
    subprocess.run(["systemctl", "daemon-reload"])
    subprocess.run(["systemctl", "restart", "pipbox.service"])
    if dji_changed:
        subprocess.run(["systemctl", "restart", "pipbox-dji.service"])
    if hdmi_changed:
        if os.path.exists(os.path.join(INSTALL, "hdmi_daemon.py")):
            subprocess.run(["systemctl", "restart", "pipbox-hdmi.service"])
        else:                                    # eine Version ohne HDMI-Dienst: Dienst und Eintrag entfernen, sonst startet er ins Leere
            subprocess.run(["systemctl", "disable", "--now", "pipbox-hdmi.service"])
            try:
                os.remove(os.path.join(UNIT_DIR, "pipbox-hdmi.service"))
            except OSError:
                pass
            subprocess.run(["systemctl", "daemon-reload"])
    ok = wait_active("pipbox.service")
    prune()
    log(f"Wechsel auf {ver}: {'ok' if ok else 'Oberfläche läuft nicht'}")
    status(state="rolledback" if ok else "failed", step="", version=ver, progress=100 if ok else 0,
           message=(f"{note} Version {ver}." + (f" Grund: {reason}" if reason else "")) if ok
           else "Wechsel fehlgeschlagen: die Oberfläche startet nicht. Bitte per SSH prüfen.")


def rollback(reason=""):
    """Zurück auf die zuletzt gesicherte Version (die ältere Seite des letzten Wechsels)."""
    cur = local_version()
    prev = [v for v in list_backups() if v != cur]
    if not prev:
        raise Refuse("Es gibt keine gesicherte Version zum Zurückrollen")
    restore(prev[0], reason)


# Schritte von install.sh (Zeilen "PIPBOX-STEP <Kennung>"): Fortschritt in Prozent und Text für die Anzeige
INSTALL_STEPS = {
    "pakete": (40, "Prüfe die benötigten Pakete"),
    "dienst": (48, "Halte die Oberfläche an und richte Benutzer und Daten ein"),
    "dateien": (58, "Kopiere Programme und Oberfläche"),
    "baustein": (66, "Prüfe den Bild-in-Bild-Baustein (wird neu gebaut, wenn er sich geändert hat)"),
    "srtla": (78, "Prüfe den SRTLA-Sender (wird neu gebaut, wenn er sich geändert hat)"),
    "belacoder": (88, "Prüfe den Encoder (wird neu gebaut, wenn er sich geändert hat)"),
    "start": (95, "Starte die Dienste neu"),
}
INSTALL_TIMEOUT = 900


def run_install(tmp):
    """install.sh ausführen und dabei die Schrittmarken in den Fortschritt übersetzen. Gibt (Rückgabecode, letzte Zeilen) zurück."""
    p = subprocess.Popen(["bash", f"{tmp}/install/install.sh", "install"], cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace", start_new_session=True)
    killed = []

    def too_long():
        killed.append(1)
        try:
            os.killpg(p.pid, 9)                       # die ganze Gruppe: Kindprozesse halten sonst die Leitung offen
        except OSError:
            p.kill()
    timer = threading.Timer(INSTALL_TIMEOUT, too_long)
    timer.start()
    tail = []
    try:
        for line in p.stdout:
            line = line.rstrip("\n")
            m = re.match(r"^PIPBOX-STEP (\w+)$", line)
            if m and m.group(1) in INSTALL_STEPS:
                pct, txt = INSTALL_STEPS[m.group(1)]
                status(step=txt, progress=pct)
                continue
            tail.append(line)
            del tail[:-25]
        p.wait()
    finally:
        timer.cancel()
    return (-9 if killed else p.returncode), tail


def install(version=None):
    """version=None: neueste Version aus dem Zweig main (nur wenn neuer). version=x.y.z: genau diese Release-Marke von GitHub."""
    if sending() and not IGNORE_SENDING:
        raise Refuse("Es wird gerade gesendet. Bitte zuerst die Übertragung beenden.")
    local = local_version()
    status(state="installing", step="Lade die neue Version von GitHub", message="", frm=local, to=version or "", progress=2)
    try:
        data = download(URL if version is None else TAG_URL.format(version), lambda pct: status(progress=2 + pct * 18 // 100))
    except urllib.error.HTTPError as e:
        raise Refuse(f"Version {version} gibt es auf GitHub nicht (Antwort {e.code})" if version else f"GitHub antwortet mit {e.code}")
    tmp = f"/var/tmp/pipbox-swupdate-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        status(step="Prüfe das Archiv", progress=22)
        n = safe_extract(data, tmp)
        new = validate(tmp, local, version)
        log(f"Archiv ok: {n} Dateien, Version {new} (installiert {local})")
        status(step="Sichere die jetzige Version", to=new, progress=30)
        backup(local, do_prune=False)
        status(step=f"Installiere Version {new}", progress=36)
        rc, tail = run_install(tmp)
        log("install.sh Ausgabe (Ende):\n" + "\n".join(tail))
        if rc != 0 or not wait_active("pipbox.service"):
            log("Installation fehlgeschlagen, rolle zurück")
            restore(local, f"Installation fehlgeschlagen (Code {rc})")
            return
        prune()
        status(state="done", step="", version=new, message=f"Version {new} ist installiert.", progress=100)
        log(f"Update auf {new} abgeschlossen")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def switch(version):
    """Gezielt auf eine Version wechseln: lokal gesichert (sofort) oder als Release-Marke von GitHub."""
    if not VERSION_RE.match(version or ""):
        raise Refuse("Ungültige Versionsnummer")
    if version == local_version():
        raise Refuse(f"Version {version} ist schon installiert")
    if sending() and not IGNORE_SENDING:
        raise Refuse("Es wird gerade gesendet. Bitte zuerst die Übertragung beenden.")
    if version in list_backups():
        restore(version, note="Gewechselt auf")
    else:
        install(version)


def main():
    global TEST_TARBALL
    global IGNORE_SENDING
    if len(sys.argv) >= 3 and sys.argv[1] == "--tarball" and os.geteuid() == 0:
        TEST_TARBALL = sys.argv[2]
        IGNORE_SENDING = "--ignore-sending" in sys.argv[3:]
    os.makedirs(RUN, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    try:
        lines = read_req(REQ).strip().splitlines()
    except OSError:
        lines = []
    mode, arg = (lines[0] if lines else ""), (lines[1].strip() if len(lines) > 1 else "")
    try:
        os.remove(REQ)
    except OSError:
        pass
    if mode not in MODES or (mode == "switch" and not VERSION_RE.match(arg)) or (mode != "switch" and arg):
        log("Unbekannte Anforderung verworfen.")
        return 0
    try:
        if mode == "install":
            install()
        elif mode == "rollback":
            rollback()
        else:
            switch(arg)
    except Refuse as e:
        log(f"abgelehnt: {e}")
        status(state="refused", step="", message=str(e), progress=0)
    except Exception as e:                                  # nie ohne Meldung enden
        log(f"Fehler: {e!r}")
        status(state="failed", step="", message=f"Fehler beim Update: {str(e)[:120]}", progress=0)
        if mode in ("install", "switch") and not wait_active("pipbox.service", 1):
            try:
                rollback("Fehler beim Update")
            except Exception as e2:
                log(f"Rollback fehlgeschlagen: {e2!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
