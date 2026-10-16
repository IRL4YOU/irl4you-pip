#!/usr/bin/env python3
"""Optionaler Root-Helfer (NUR LOKAL, nicht Teil von install.sh, nicht auf GitHub): neuerer Treiber für WLAN-Sticks mit den Chips RTL8812BU/RTL8822BU (Treiber 88x2bu).

Warum: Der Kernel 5.10.160 der BELABOX bringt den Treiber 88x2bu (Stand Juli 2021) mit. Das Projekt morrownr/88x2bu-20210702 pflegt ihn weiter (zuletzt September 2026);
seit 2021 stehen dort unter anderem Verbesserungen für den Hotspot-Betrieb (gleichzeitiger Betrieb von Client und Hotspot, MU-MIMO, WPA3, ein behobener Hänger beim
Entfernen der Schnittstelle, kein TDLS). Dieser Helfer baut genau einen festen Stand (COMMIT) für den laufenden Kernel und legt ihn als Überlagerung ab; der eingebaute
Treiber bleibt unberührt und gilt wieder, sobald die Überlagerung weg ist.

Wie es sicher bleibt:
  * Die Überlagerung liegt in /lib/modules/<Kernel>/updates/. Nach einem Kernel-Update gibt es diesen Ordner für den neuen Kernel nicht: dann gilt von selbst wieder der
    eingebaute Treiber (kein Neubau nötig, kein Fehlstart).
  * Gebaut wird in einem Temporärordner mit niedriger Priorität. Vorher wird die Quelle gegen eine feste Summe über Pfade und Inhalte geprüft (TREE_SHA256).
  * Nichts passiert während einer Sendung (Ausnahme: --force). Das Neuladen des Treibers betrifft nur Sticks mit diesem Treiber; hängt ein Hotspot darauf, bricht er kurz ab.
  * Rückweg: `uninstall` löscht die Überlagerung und die Moduleinstellung, führt depmod aus und lädt den eingebauten Treiber wieder.

Aufruf (als root):
  status                     zeigt, welcher Treiber gilt und ob die Überlagerung da ist
  install [--src Datei.tgz]  prüft, baut, legt ab (Quelle sonst von GitHub); lädt NICHT neu
  reload [--force]           lädt den Treiber neu (Stick abziehen und stecken tut dasselbe)
  uninstall                  entfernt die Überlagerung und stellt den eingebauten Treiber wieder her
Optionen: --force (auch während einer Sendung), --options "rtw_power_mgnt=0 …" (statt der Voreinstellung OPTIONS)
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

REPO = "morrownr/88x2bu-20210702"
COMMIT = "fe48647496798cac77976e310ee95da000b436c9"          # 29. Mai 2025: nach dem Hänger-Fix (Juni 2024) und MU-MIMO (Januar 2023), vor den Anpassungen an Kernel 6.17 und neuer
URL = f"https://codeload.github.com/{REPO}/tar.gz/{COMMIT}"
TREE_SHA256 = "efafe40192e837e486895f83d53943293300db882ede89d29989eac87ed29da9"   # über Pfade und Dateiinhalte (696 Dateien)
MAX_DOWNLOAD = 30 * 1024 * 1024
MAX_UNPACKED = 60 * 1024 * 1024
SRC_KERNEL = "5.10.160"                                        # nur für diesen Kernel geprüft
MODULE = "88x2bu"
LIB_MODULES = "/lib/modules"
MODPROBE_D = "/etc/modprobe.d"
CONF = f"{MODPROBE_D}/pipbox-88x2bu.conf"
# Hotspot: nicht maximal Strom sparen (für den AP-Modus nicht empfohlen), 80 MHz im AP-Modus freigeben, kein USB-3-Umschalten
OPTIONS = "rtw_power_mgnt=0 rtw_vht_enable=2 rtw_switch_usb_mode=0"
LOCK = "/run/pipbox-88x2bu.lock"
SEND_STATUS = "/run/pipbox-send/status.json"
TMPROOT = "/var/tmp"
BUILD_TIMEOUT = 900
SYSFS_NET = "/sys/class/net"


def say(msg):
    print(msg, flush=True)


def run(args, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("timeout", 60)
    return subprocess.run(args, **kw)


def kernel():
    return os.uname().release


def tree_sha256(tar):
    """Summe über relative Pfade und Inhalte aller Dateien eines entpackbaren Archivs (ohne den obersten Ordner)."""
    h, n = hashlib.sha256(), 0
    members = sorted((m for m in tar.getmembers() if m.isfile()), key=lambda m: m.name.split("/", 1)[-1])
    for m in members:
        h.update(m.name.split("/", 1)[-1].encode() + b"\0" + hashlib.sha256(tar.extractfile(m).read()).digest())
        n += 1
    return h.hexdigest(), n


def sending():
    """Läuft gerade eine Sendung (Sendekette)? Unlesbar gilt als nicht sendend."""
    try:
        with open(SEND_STATUS) as f:
            return json.load(f).get("state") == "running"
    except (OSError, ValueError):
        return False


def overlay_path(rel=None):
    return os.path.join(LIB_MODULES, rel or kernel(), "updates", MODULE + ".ko")


def module_file():
    r = run(["modinfo", "-F", "filename", MODULE])
    return r.stdout.strip() if r.returncode == 0 else ""


def cards():
    """Schnittstellen, die gerade den Treiber 88x2bu benutzen."""
    out = []
    try:
        names = sorted(os.listdir(SYSFS_NET))
    except OSError:
        return out
    for n in names:
        try:
            if os.path.basename(os.readlink(f"{SYSFS_NET}/{n}/device/driver")) in (MODULE, "rtl" + MODULE):          # der USB-Treiber heißt rtl88x2bu
                out.append(n)
        except OSError:
            pass
    return out


def status():
    f = module_file()
    return {"kernel": kernel(), "overlay": os.path.exists(overlay_path()), "modul_datei": f, "gilt_ueberlagerung": f == overlay_path(),
            "einstellungen": os.path.exists(CONF), "karten": cards(), "commit": COMMIT[:9]}


def check_prereqs(force=False):
    if os.geteuid() != 0:
        raise RuntimeError("Bitte als root ausführen")
    if kernel().split("-")[0] != SRC_KERNEL or os.uname().machine != "aarch64":
        raise RuntimeError(f"Nur für Kernel {SRC_KERNEL} (arm64) geprüft, hier läuft {kernel()} ({os.uname().machine})")
    for tool in ("gcc", "make", "depmod", "modinfo"):
        if not shutil.which(tool):
            raise RuntimeError(f"Werkzeug fehlt: {tool}")
    if not os.path.isdir(os.path.join(LIB_MODULES, kernel(), "build")):
        raise RuntimeError("Die Kernel-Quellen (/lib/modules/…/build) fehlen")
    if sending() and not force:
        raise RuntimeError("Es wird gerade gesendet. Nach der Sendung noch einmal aufrufen (oder --force)")


def fetch(src=None):
    """Das Archiv als Bytes: aus einer Datei oder von GitHub (mit Größengrenze)."""
    if src:
        with open(src, "rb") as f:
            return f.read(MAX_DOWNLOAD + 1)
    with urllib.request.urlopen(URL, timeout=60) as r:
        return r.read(MAX_DOWNLOAD + 1)


def unpack(data, dest):
    path = os.path.join(dest, "src.tgz")
    if len(data) > MAX_DOWNLOAD:
        raise RuntimeError("Das Archiv ist zu groß")
    with open(path, "wb") as f:
        f.write(data)
    with tarfile.open(path) as t:
        total = sum(m.size for m in t.getmembers() if m.isfile())
        bad = [m.name for m in t.getmembers() if m.issym() or m.islnk() or m.name.startswith(("/", "..")) or ".." in m.name.split("/")]
        if bad or total > MAX_UNPACKED:
            raise RuntimeError("Das Archiv enthält Unerwartetes")
        digest, n = tree_sha256(t)
        if digest != TREE_SHA256:
            raise RuntimeError(f"Die Quelle stimmt nicht mit dem geprüften Stand überein ({digest[:12]} statt {TREE_SHA256[:12]})")
        out = os.path.join(dest, "src")
        os.makedirs(out)
        for m in t.getmembers():
            if m.isfile():
                m.name = m.name.split("/", 1)[-1]
                t.extract(m, out)
    return out


def build(srcdir):
    ksrc = os.path.join(LIB_MODULES, kernel(), "build")
    log = os.path.join(srcdir, "build.log")
    with open(log, "w") as f:
        r = subprocess.run(["nice", "-n", "19", "make", "-j2", "ARCH=arm64", f"KVER={kernel()}", f"KSRC={ksrc}", "modules"], cwd=srcdir, stdout=f, stderr=subprocess.STDOUT,
                           timeout=BUILD_TIMEOUT)
    ko = os.path.join(srcdir, MODULE + ".ko")
    if r.returncode != 0 or not os.path.exists(ko):
        tail = "".join(open(log, errors="replace").readlines()[-6:])
        raise RuntimeError("Der Bau ist fehlgeschlagen:\n" + tail)
    info = run(["modinfo", ko]).stdout
    if not re.search(r"^vermagic:\s+" + re.escape(kernel()) + r"\b", info, re.M) or not re.search(r"^alias:\s+usb:v0BDApB812d", info, re.M | re.I):      # Chip-Kennung 0bda:b812 steht als usb:v0BDApB812
        raise RuntimeError("Das gebaute Modul passt nicht zum Kernel oder kennt den Chip nicht")
    return ko


def other_conf_sets_options():
    """Hat jemand anderes Optionen für den Treiber gesetzt? Dann bleibt das unberührt."""
    d = MODPROBE_D
    try:
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if p != CONF and n.endswith(".conf") and re.search(r"^\s*options\s+" + MODULE + r"\b", open(p, errors="replace").read(), re.M):
                return p
    except OSError:
        pass
    return ""


def install(src=None, force=False, options=OPTIONS):
    check_prereqs(force)
    lock = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise RuntimeError("Es läuft schon ein Aufruf")
    work = tempfile.mkdtemp(prefix="pipbox-88x2bu-", dir=TMPROOT)
    try:
        say("Quelle holen und prüfen …")
        srcdir = unpack(fetch(src), work)
        say("Bauen (etwa 1 bis 2 Minuten, niedrige Priorität) …")
        ko = build(srcdir)
        dest = overlay_path()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(ko, dest + ".neu")
        os.replace(dest + ".neu", dest)
        os.chmod(dest, 0o644)
        other = other_conf_sets_options()
        if options and not other:
            with open(CONF, "w") as f:
                f.write(f"# IRL4YOU BOX: Einstellungen für den Hotspot-Betrieb des Treibers 88x2bu (zurück: pipbox-88x2bu.py uninstall)\noptions {MODULE} {options}\n")
        elif other:
            say(f"Hinweis: {other} setzt schon Optionen für {MODULE}, sie bleiben unberührt.")
        r = run(["depmod", "-a", kernel()], timeout=120)
        if r.returncode != 0 or module_file() != dest:
            os.unlink(dest)
            run(["depmod", "-a", kernel()], timeout=120)
            raise RuntimeError("Die Überlagerung wird nicht gewählt (depmod): zurückgenommen")
        say(f"Fertig: {dest}\nNeu laden mit „reload“ (oder Stick abziehen und stecken, oder Neustart).")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        os.close(lock)


def reload(force=False):
    if sending() and not force:
        raise RuntimeError("Es wird gerade gesendet; Neuladen betrifft nur Sticks mit diesem Treiber, aber nicht mitten in einer Sendung (--force, wenn die Sendung keinen davon benutzt)")
    used = cards()
    if used:
        say("Karten mit diesem Treiber (ein laufender Hotspot darauf bricht kurz ab): " + ", ".join(used))
    r = run(["modprobe", "-r", MODULE], timeout=60)
    if r.returncode != 0:
        raise RuntimeError("Entladen nicht möglich (Karte in Gebrauch?): " + (r.stderr.strip()[:160] or "unbekannt"))
    r = run(["modprobe", MODULE], timeout=60)
    if r.returncode != 0:
        raise RuntimeError("Laden fehlgeschlagen: " + (r.stderr.strip()[:160] or "unbekannt"))
    say("Geladen: " + (module_file() or "?"))


def uninstall():
    for p in (overlay_path(), CONF):
        try:
            os.unlink(p)
            say("Gelöscht: " + p)
        except FileNotFoundError:
            pass
    try:
        os.rmdir(os.path.dirname(overlay_path()))
    except OSError:
        pass
    run(["depmod", "-a", kernel()], timeout=120)
    say("Es gilt wieder: " + (module_file() or "der eingebaute Treiber"))
    say("Neu laden mit „reload“ (oder Stick abziehen und stecken, oder Neustart), damit der eingebaute Treiber läuft.")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    force = "--force" in argv
    src = argv[argv.index("--src") + 1] if "--src" in argv and argv.index("--src") + 1 < len(argv) else None
    options = argv[argv.index("--options") + 1] if "--options" in argv and argv.index("--options") + 1 < len(argv) else OPTIONS
    try:
        if cmd == "status":
            print(json.dumps(status(), indent=1, ensure_ascii=False))
        elif cmd == "install":
            install(src, force, options)
        elif cmd == "reload":
            if os.geteuid() != 0:
                raise RuntimeError("Bitte als root ausführen")
            reload(force)
        elif cmd == "uninstall":
            if os.geteuid() != 0:
                raise RuntimeError("Bitte als root ausführen")
            uninstall()
        else:
            print(__doc__)
            return 2
    except (RuntimeError, OSError, subprocess.SubprocessError, tarfile.TarError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
