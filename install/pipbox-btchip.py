#!/usr/bin/env python3
"""Root-Helfer: Bluetooth des eingebauten Realtek-Moduls der ROCK 5B Plus (nur dort).

Auf der ROCK 5B Plus sitzt der Bluetooth-Teil des WLAN-Moduls RTL8852BE als USB-Gerät 13d3:3572. Der Kernel 5.10 der BELABOX meldet ihn am
allgemeinen Treiber btusb an, der für diesen Chip keine Firmware lädt: Der Controller zeigt "UP RUNNING", empfängt aber nichts (die Suche findet keine
Kamera, kein Handy, gar nichts). Der Realtek-Herstellertreiber rtk_btusb gehört zum Kernel der BELABOX und lädt die Firmware, sucht sie aber ohne Ordner und ohne
".bin" unter /lib/firmware. Dieser Helfer legt die beiden Verknüpfungen an (rtl8852bu_fw und rtl8852bu_config auf die Dateien in rtl_bt/) und hängt den Chip vom
allgemeinen Treiber auf rtk_btusb um. Das Standardmodul btusb bleibt unberührt; "Rückweg" ist ein Neustart ohne den Helfer oder "uninstall".

Er tut nur etwas, wenn alle drei Bedingungen stimmen: das Board heißt "Radxa ROCK 5B Plus", das USB-Gerät 13d3:3572 steckt und der Kernel ist ein 5.10.
Auf jedem anderen Gerät (zum Beispiel der Orange Pi 5 Plus) endet er sofort, ohne etwas zu ändern.

Aufruf (nur durch pipbox-btchip.service, keine Eingaben von außen):
  auto       beim Start, beim Erkennen des Chips (udev) und auf die Auslösedatei der Oberfläche (Knopf "Treiber jetzt einrichten"; dann wird auch ein
             schon eingerichteter Chip neu geprüft und ein früherer Fehlschlag noch einmal versucht).
  uninstall  entfernt die beiden Verknüpfungen (nur wenn sie von uns stammen)

Sicherheitsnetz: Während einer Übertragung (belacoder läuft) wird nichts umgehängt. Nach dem Umhängen muss ein Bluetooth-Adapter am Chip auftauchen und die Firmware ohne
Fehler geladen werden, sonst wird der Chip wieder an btusb zurückgegeben und für diesen Kernel nicht noch einmal versucht (außer per Knopf).
"""
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time

BOARD = "Radxa ROCK 5B Plus"
USB_ID = "13d3:3572"
USB_NAME = "Realtek (RTL8852BE, eingebaut)"
KERNEL_PREFIX = "5.10."
GENERIC = "btusb"
VENDOR = "rtk_btusb"
FW_LINKS = {"rtl8852bu_fw": "rtl_bt/rtl8852bu_fw.bin", "rtl8852bu_config": "rtl_bt/rtl8852bu_config.bin"}
FIRMWARE_DIR = "/lib/firmware"
DT_MODEL = "/proc/device-tree/model"
SYSFS_USB = "/sys/bus/usb/devices"
SYSFS_DRV = "/sys/bus/usb/drivers"
SYSFS_BT = "/sys/class/bluetooth"
RUN = "/run/pipbox-btchip"
STATUS = f"{RUN}/status.json"
LOCK = "/run/pipbox-btchip.lock"
REQ = "/var/lib/pipbox/btchip-request"       # legt der Dienst pipbox (ohne Root) per Knopf in der Oberfläche an; Inhalt nur "install"
PERSIST = "/var/lib/pipbox-btchip"            # gehört root: Merkzettel "hat nicht geklappt"
WAIT_ADAPTER = 25                             # Sekunden, bis nach dem Umhängen ein Adapter am Chip auftauchen muss


def log(msg):
    print(f"pipbox-btchip: {msg}", flush=True)


def run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def write_sysfs(path, text):
    with open(path, "w") as f:
        f.write(text)


def read_text(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def status(**kw):
    kw.setdefault("time", int(time.time()))
    try:
        write_json(STATUS, kw)
    except OSError as e:
        log(f"Status nicht geschrieben: {e}")


def release():
    return os.uname().release


def board_ok():
    return read_text(DT_MODEL).replace("\0", "").strip() == BOARD


def chip_device():
    """Name des USB-Geräts (z. B. "1-1.3") des eingebauten Moduls oder None."""
    try:
        names = sorted(os.listdir(SYSFS_USB))
    except OSError:
        return None
    vid, pid = USB_ID.split(":")
    for n in names:
        if ":" in n:
            continue
        d = os.path.join(SYSFS_USB, n)
        if read_text(os.path.join(d, "idVendor")).strip().lower() == vid and read_text(os.path.join(d, "idProduct")).strip().lower() == pid:
            return n
    return None


def interfaces(dev):
    d = os.path.join(SYSFS_USB, dev)
    try:
        return sorted(n for n in os.listdir(d) if n.startswith(dev + ":"))
    except OSError:
        return []


def driver_of(iface):
    link = os.path.join(SYSFS_USB, iface, "driver")
    return os.path.basename(os.path.realpath(link)) if os.path.islink(link) else ""


def hci_of(dev):
    """hciN des Adapters, der an diesem USB-Gerät hängt, oder None."""
    base = os.path.realpath(os.path.join(SYSFS_USB, dev))
    try:
        names = os.listdir(SYSFS_BT)
    except OSError:
        return None
    for n in sorted(names):
        if re.fullmatch(r"hci\d+", n) and os.path.realpath(os.path.join(SYSFS_BT, n, "device")).startswith(base + os.sep):
            return n
    return None


def belacoder_running():
    try:
        for d in os.listdir("/proc"):
            if d.isdigit() and read_text(f"/proc/{d}/comm").strip() == "belacoder":
                return True
    except OSError:
        pass
    return False


def dmesg_lines():
    r = run(["dmesg"], timeout=20)
    return r.stdout.splitlines() if r.returncode == 0 else []


def firmware_errors(lines):
    return [x for x in lines if re.search(r"load firmware failed|Direct firmware load for rtl8852bu\w* failed|Rtk patch end -\d", x)]


def firmware_present():
    return all(os.path.isfile(os.path.join(FIRMWARE_DIR, t)) for t in FW_LINKS.values())


def ensure_links():
    """Legt die Verknüpfungen an. Eine vorhandene echte Datei bleibt unberührt, eine fremde Verknüpfung wird nicht überschrieben."""
    for name, target in FW_LINKS.items():
        link = os.path.join(FIRMWARE_DIR, name)
        if os.path.islink(link):
            if os.readlink(link) == target:
                continue
            raise RuntimeError(f"{name} zeigt auf etwas anderes")
        if os.path.exists(link):
            continue
        os.symlink(target, link)


def signature():
    return hashlib.sha256((release() + "|" + os.uname().version + "|" + USB_ID).encode()).hexdigest()[:16]


def take_request():
    try:
        os.remove(REQ)
        return True
    except OSError:
        return False


def attached(dev):
    ifs = interfaces(dev)
    return bool(ifs) and all(driver_of(i) == VENDOR for i in ifs) and hci_of(dev) is not None


def attach(dev):
    """Hängt die Schnittstellen des Chips von btusb auf rtk_btusb um (Kennung dem Herstellertreiber bekannt machen, dann binden)."""
    run(["modprobe", VENDOR], timeout=60)
    if not os.path.isdir(os.path.join(SYSFS_DRV, VENDOR)):
        raise RuntimeError(f"Der Herstellertreiber {VENDOR} ist nicht verfügbar")
    ifs = interfaces(dev)
    for i in ifs:
        if driver_of(i) == GENERIC:
            write_sysfs(os.path.join(SYSFS_DRV, GENERIC, "unbind"), i)
    try:
        write_sysfs(os.path.join(SYSFS_DRV, VENDOR, "new_id"), USB_ID.replace(":", " "))
    except OSError:
        pass                                                           # Kennung ist dem Treiber schon bekannt
    for i in ifs:
        if driver_of(i) != VENDOR:
            try:
                write_sysfs(os.path.join(SYSFS_DRV, VENDOR, "bind"), i)
            except OSError:
                pass                                                   # new_id hat schon gebunden


def restore(dev):
    """Gibt den Chip an den allgemeinen Treiber zurück (nach einem Fehlschlag)."""
    for i in interfaces(dev):
        if driver_of(i) == VENDOR:
            try:
                write_sysfs(os.path.join(SYSFS_DRV, VENDOR, "unbind"), i)
            except OSError:
                pass
    for i in interfaces(dev):
        if not driver_of(i):
            try:
                write_sysfs(os.path.join(SYSFS_DRV, GENERIC, "bind"), i)
            except OSError:
                pass


def wait_adapter(dev, seconds=WAIT_ADAPTER):
    end = time.time() + seconds
    while time.time() < end:
        if hci_of(dev):
            return True
        time.sleep(0.5)
    return bool(hci_of(dev))


def do_auto(force=False):
    if not board_ok():
        return 0                                                       # nicht die ROCK 5B Plus: nichts tun, auch keine Meldung
    dev = chip_device()
    if not dev:
        return 0

    def st(**kw):
        status(manual=bool(force), applies=True, name=USB_NAME, **kw)
    rel = release()
    if not rel.startswith(KERNEL_PREFIX):
        st(state="unsupported", message=f"Für den Kernel {rel} ist der Bluetooth-Chip auf dem Board nicht vorbereitet.")
        return 0
    marker = os.path.join(PERSIST, "failed.json")
    if attached(dev):
        try:
            ensure_links()
        except (OSError, RuntimeError) as e:
            log(f"Verknüpfungen: {e}")
        st(state="ok", message=f"Der Bluetooth-Chip auf dem Board ({USB_NAME}, {USB_ID}) ist eingerichtet und läuft mit dem Herstellertreiber. Ein Stick ist nicht nötig.")
        return 0
    if not force and read_json(marker).get("signature") == signature():
        return 0                                                       # hat bei diesem Kernel nicht geklappt: nicht in einer Schleife versuchen
    if belacoder_running():
        st(state="waiting", message="Der Bluetooth-Chip auf dem Board wird nach der Übertragung eingerichtet (Bluetooth fehlt dabei kurz).")
        return 0
    st(state="working", message="Der Bluetooth-Chip auf dem Board wird eingerichtet …")
    before = len(dmesg_lines())
    try:
        if not firmware_present():
            raise RuntimeError("Die Firmware (rtl_bt/rtl8852bu_fw.bin) fehlt")
        ensure_links()
        attach(dev)
        if not wait_adapter(dev):
            raise RuntimeError("Nach dem Umhängen gibt es keinen Bluetooth-Adapter")
        bad = firmware_errors(dmesg_lines()[before:])
        if bad:
            raise RuntimeError("Die Firmware ließ sich nicht laden: " + bad[-1][-100:])
    except Exception as e:
        log(f"Chip nicht eingerichtet: {e!r}")
        try:
            restore(dev)
        except Exception as e2:
            log(f"Rückgabe an {GENERIC} fehlgeschlagen: {e2!r}")
        try:
            write_json(marker, {"signature": signature(), "message": str(e)[:200]})
        except OSError:
            pass
        st(state="failed", message=f"Der Bluetooth-Chip auf dem Board ließ sich nicht einrichten, es läuft der Standardtreiber weiter: {str(e)[:160]}")
        return 0
    try:
        os.remove(marker)
    except OSError:
        pass
    log(f"Chip {USB_ID} auf {VENDOR} umgestellt")
    st(state="ok", message=f"Der Bluetooth-Chip auf dem Board ({USB_NAME}, {USB_ID}) ist eingerichtet und läuft mit dem Herstellertreiber. Ein Stick ist nicht nötig.")
    return 0


def do_uninstall():
    for name, target in FW_LINKS.items():
        link = os.path.join(FIRMWARE_DIR, name)
        if os.path.islink(link) and os.readlink(link) == target:
            os.remove(link)
    for p in (STATUS, os.path.join(PERSIST, "failed.json")):
        try:
            os.remove(p)
        except OSError:
            pass
    log("Verknüpfungen entfernt; nach dem nächsten Neustart gilt wieder der allgemeine Treiber")
    return 0


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if mode not in ("auto", "uninstall"):
        log("Unbekannte Anforderung verworfen.")
        return 0
    os.makedirs(RUN, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    try:
        return do_auto(force=take_request()) if mode == "auto" else do_uninstall()
    except Exception as e:                                                # nie ohne Meldung enden
        log(f"Fehler: {e!r}")
        if board_ok():
            status(state="failed", applies=True, name=USB_NAME, message=str(e)[:200])
        return 0


if __name__ == "__main__":
    sys.exit(main())
