#!/usr/bin/env python3
"""Root-Helfer: Treiber für Bluetooth-Sticks, die der Kernel 5.10 nicht richtig kennt (Realtek, zum Beispiel TP-Link UB500, und Barrot, zum Beispiel
UGREEN BT6.0).

Der Kernel 5.10 der BELABOX kennt einige Realtek-Sticks (RTL8761B/BU) nicht in seiner Tabelle. Solche Sticks starten dann ohne
Firmware, finden keine Kameras und wirken tot. Dieser Helfer baut aus den mitgelieferten, unveränderten Kernelquellen (GPL-2.0,
Ordner /opt/pipbox/btusb-src, aus dem Kernel v5.10.160) das Modul btusb neu, mit einigen zusätzlichen Kennungen, und spielt es
nach /lib/modules/<Kernel>/updates/ ein. Das Standardmodul bleibt unberührt auf der Platte; "Rückweg" ist, die eine Datei zu löschen.

Barrot-Sticks (zum Beispiel UGREEN BT6.0, 33fa:0012) schicken nach der Antwort auf "Read Local Extended Features" (genau 16 Byte, so groß wie ein
USB-Paket) ein einzelnes Zufallsbyte hinterher. Es kommt als eigenes, ein Byte langes Paket vor der nächsten Antwort an; ab dann liegt jede Antwort um ein
Byte verschoben, der Start des Adapters scheitert und er bleibt auf DOWN (am USB-Mitschnitt gemessen, Kernel 5.10.160, RK3588). Der Kernel hat das mit dem
Commit 7722d6fb54 ("Bluetooth: btusb: Check for unexpected bytes when defragmenting HCI frames", getestet mit genau diesem Stick) für Überhang im selben
Paket behoben. Der Helfer übernimmt diese Prüfung und ergänzt die zweite für das einzelne Byte als eigenes Paket (`patch_recv_intr`): Ein neues Ereignis
beginnt nie mit weniger Bytes als ein Ereigniskopf. Der Stick selbst braucht keine Firmware.

Aufruf (nur durch pipbox-btdriver.service/.timer, keine Eingaben von außen):
  auto       prüft, ob ein Stick steckt, der den Treiber braucht, und richtet ihn dann ein (udev beim Einstecken, Zeitgeber alle 15 min)
  uninstall  entfernt das eingespielte Modul (das Standardmodul gilt nach dem nächsten Neustart wieder)

Sicherheitsnetz: gebaut wird in einem Temporärordner; geprüft werden Kernel, Werkzeuge und die Modulversion; während einer Übertragung
passiert nichts (der Ablauf lädt das Bluetooth-Modul neu); nach dem Laden muss ein Adapter da sein und die Firmware ohne Fehler
geladen werden, sonst wird alles zurückgerollt und für diese Kombination nicht noch einmal versucht. Schlägt etwas fehl, läuft alles
mit dem Standardtreiber weiter.
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

SRC = "/opt/pipbox/btusb-src"
SRC_FILES = ("btusb.c", "btintel.h", "btbcm.h", "btrtl.h")
SRC_KERNEL = "5.10.160"                      # aus diesem Kernel stammen die Quellen; nur dafür wird gebaut
LIB_MODULES = "/lib/modules"
RUN = "/run/pipbox-btdriver"
STATUS = f"{RUN}/status.json"
LOCK = "/run/pipbox-btdriver.lock"
PERSIST = "/var/lib/pipbox-btdriver"         # gehört root: Merkzettel "hat nicht geklappt" und "eingespielt"
SYSFS_USB = "/sys/bus/usb/devices"
ANCHOR = "\t{ USB_DEVICE(0x0bda, 0xb009), .driver_info = BTUSB_REALTEK },\n"
BUILD_TIMEOUT = 900

# Sticks, für die der Treiber nötig ist (USB-Kennung: Bezeichnung). Löst beim Einstecken den Bau aus.
CANDIDATES = {
    "2357:0604": "TP-Link UB500 (Realtek RTL8761BUV)",
    "2550:8761": "Realtek RTL8761B (2550:8761)",
    "2c4e:0115": "Mercusys MA530 (Realtek RTL8761B)",
    "0bda:8771": "Realtek RTL8761B (0bda:8771)",
    "0bda:a725": "Realtek (0bda:a725)",
    "2b89:8761": "Realtek RTL8761B (2b89:8761)",
}
# Barrot-Sticks: kein Eintrag in der Tabelle nötig (sie laufen über die Geräteklasse am Standardtreiber), aber die Prüfung der Ereignisse (siehe oben).
BARROT = {
    "33fa:0012": "UGREEN BT6.0 Adapter (Barrot)",
    "33fa:0010": "Barrot Bluetooth-Stick (33fa:0010)",
}
CANDIDATES.update(BARROT)
# Zusätzlich im Treiber eingetragen, löst aber nichts aus: läuft auch ohne (ASUS USB-BT500), bekommt mit dem Treiber die Firmware.
EXTRA_IDS = {"0b05:190e": "ASUS USB-BT500 (Realtek RTL8761B)"}
ALL_IDS = {**CANDIDATES, **EXTRA_IDS}
REALTEK_IDS = {i: n for i, n in ALL_IDS.items() if i not in BARROT}          # nur diese kommen mit BTUSB_REALTEK in die Tabelle des Treibers
# Stelle in btusb_recv_intr (Kernel 5.10.160): hier ist ein Ereignis vollständig, und es können noch Bytes übrig sein
# C-Quelltext als ASCII-Bytes (kein Oberflächentext, bleibt aus der Übersetzung heraus)
RECV_ANCHOR = b"\t\tif (!hci_skb_expect(skb)) {\n\t\t\t/* Complete frame */\n\t\t\tdata->recv_event(data->hdev, skb);\n".decode("ascii")
COMPLETE_MARK = b"\t\t\t/* Complete frame */\n".decode("ascii")
IF_NO_SKB = b"\t\tif (!skb) {\n".decode("ascii")
DONE_MARKS = (b"Unexpected continuation".decode("ascii"), b"Unexpected stray byte".decode("ascii"))     # schon eingebaut, wenn beide im Text stehen
RECV_START_ANCHOR = b"\t\tif (!skb) {\n\t\t\tskb = bt_skb_alloc(HCI_MAX_EVENT_SIZE, GFP_ATOMIC);\n".decode("ascii")
RECV_START_GUARD = (
    b"\t\t\t/* IRL4YOU BOX: a new event always starts with the complete header. A single byte as a packet of its own is a\n"
    b"\t\t\t * bug of the stick (Barrot appends it after a 16 byte reply): drop it, or every further reply is shifted. */\n"
    b"\t\t\tif (count < HCI_EVENT_HDR_SIZE) {\n"
    b"\t\t\t\tbt_dev_warn(data->hdev, \"Unexpected stray byte: %d bytes\", count);\n"
    b"\t\t\t\tbreak;\n"
    b"\t\t\t}\n\n").decode("ascii")
RECV_GUARD = (
    b"\t\t\t/* IRL4YOU BOX, after Linux 7722d6fb54: every data packet belongs to at least one event. If fewer bytes than an event\n"
    b"\t\t\t * header are left after a complete event, that is a bug of the stick (Barrot sends one byte too many): drop them,\n"
    b"\t\t\t * or every further reply is shifted by one byte and the start of the adapter fails. */\n"
    b"\t\t\tif (count && count < HCI_EVENT_HDR_SIZE) {\n"
    b"\t\t\t\tbt_dev_warn(data->hdev, \"Unexpected continuation: %d bytes\", count);\n"
    b"\t\t\t\tcount = 0;\n"
    b"\t\t\t}\n\n").decode("ascii")

# SHA-256 der mitgelieferten Quellen (unverändert aus dem Kernel v5.10.160, von zwei Servern verglichen)
SRC_SHA256 = {
    "btusb.c": "5dbce6032028414fe89067e13ba3e3d4bf9dfb5a0e592771d8c35f68dfd50626",
    "btintel.h": "fa261ff6177abfe2d29fed0af0aa93344d0f11c51cb9ea7a9804563dc11ae5db",
    "btbcm.h": "5006497bf37ef95acc186f7c62b51ddbaaecdfcf09bb46f2d250dbc39c93f0b4",
    "btrtl.h": "759be416a2bbe7d20e98b06f1273b8a7e654770251865e7a049fe6a9c5700298",
}


def log(msg):
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


def run(args, timeout=60, **kw):
    return subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout, **kw)


def release():
    return os.uname().release


def kernel_supported(rel=None):
    return (rel or release()).split("-")[0] == SRC_KERNEL


def usb_ids_present(root=None):
    out = set()
    root = root or SYSFS_USB
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for n in names:
        if ":" in n:
            continue
        try:
            with open(f"{root}/{n}/idVendor") as f:
                v = f.read().strip().lower()
            with open(f"{root}/{n}/idProduct") as f:
                p = f.read().strip().lower()
        except OSError:
            continue
        out.add(f"{v}:{p}")
    return out


def wanted(present=None):
    """Steckende Sticks, die den Treiber brauchen: {Kennung: Bezeichnung}."""
    present = usb_ids_present() if present is None else present
    return {i: CANDIDATES[i] for i in sorted(CANDIDATES) if i in present}


def patch_recv_intr(text):
    """Baut die Prüfungen auf überzählige Bytes in btusb_recv_intr ein (Barrot-Sticks): Überhang nach einem Ereignis (Linux 7722d6fb54) und ein einzelnes
    Byte als eigenes Datenpaket am Anfang eines Ereignisses. Schon eingebaut: unverändert."""
    if all(m in text for m in DONE_MARKS):
        return text
    if text.count(RECV_ANCHOR) != 1 or text.count(RECV_START_ANCHOR) != 1:
        raise RuntimeError("btusb.c hat nicht den erwarteten Aufbau (Ankerpunkt fehlt)")
    head, tail = RECV_ANCHOR.split(COMPLETE_MARK, 1)
    text = text.replace(RECV_ANCHOR, head + RECV_GUARD + COMPLETE_MARK + tail, 1)
    return text.replace(RECV_START_ANCHOR, IF_NO_SKB + RECV_START_GUARD + RECV_START_ANCHOR.split("\n", 1)[1], 1)


def patch_source(text, ids=None):
    """Trägt die zusätzlichen Kennungen hinter dem Ankerpunkt in btusb.c ein (schon vorhandene werden übersprungen)."""
    ids = list(REALTEK_IDS if ids is None else ids)
    if text.count(ANCHOR) != 1:
        raise RuntimeError("btusb.c hat nicht den erwarteten Aufbau (Ankerpunkt fehlt)")
    lines = []
    for i in ids:
        v, p = i.split(":")
        if re.search(rf"USB_DEVICE\(0x{v}, 0x{p}\)", text, re.I):
            continue
        lines.append(f"\t{{ USB_DEVICE(0x{v}, 0x{p}), .driver_info = BTUSB_REALTEK }},\n")
    if not lines:
        return text
    return text.replace(ANCHOR, ANCHOR + "\n\t/* IRL4YOU BOX: weitere Realtek-Sticks (RTL8761B/BU), vom Kernel nicht erkannt */\n" + "".join(lines), 1)


def check_source(src=None):
    """Sind die mitgelieferten Quellen unverändert? (SHA-256)"""
    src = src or SRC
    for name in SRC_FILES:
        try:
            with open(os.path.join(src, name), "rb") as f:
                h = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            raise RuntimeError(f"Quelle {name} fehlt")
        if h != SRC_SHA256[name]:
            raise RuntimeError(f"Quelle {name} ist nicht die erwartete")


def module_path(rel=None):
    return f"{LIB_MODULES}/{rel or release()}/updates/btusb.ko"


def marker(name):
    return os.path.join(PERSIST, name)


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


def signature(rel, ids):
    return hashlib.sha256((rel + "|" + ",".join(sorted(ids))).encode()).hexdigest()[:16]


def installed_ok(rel=None, ids=None):
    """Ist unser Modul für diesen Kernel eingespielt und deckt es die gewünschten Sticks ab?"""
    rel = rel or release()
    rec = read_json(marker("installed.json"))
    return (os.path.isfile(module_path(rel)) and rec.get("kernel") == rel
            and set(ids or ()) <= set(rec.get("ids", [])))


def belacoder_running():
    try:
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    with open(f"/proc/{d}/comm") as f:
                        if f.read().strip() == "belacoder":
                            return True
                except OSError:
                    pass
    except OSError:
        pass
    return False


def tools_ok(rel=None):
    rel = rel or release()
    miss = [t for t in ("make", "gcc") if not shutil.which(t)]
    build = f"{LIB_MODULES}/{rel}/build"
    for f in ("Makefile", "Module.symvers", ".config", "scripts/mod/modpost"):
        if not os.path.exists(os.path.join(build, f)):
            miss.append(f"Kernel-Header ({f})")
            break
    return miss


def build_module(rel=None, ids=None):
    """Baut btusb.ko in einem Temporärordner und gibt den Pfad der Datei zurück (der Ordner bleibt bis zum Aufräumen)."""
    rel = rel or release()
    check_source()
    tmp = tempfile.mkdtemp(prefix="pipbox-btdriver-", dir="/var/tmp")
    try:
        for name in SRC_FILES:
            shutil.copy(os.path.join(SRC, name), os.path.join(tmp, name))
        with open(os.path.join(tmp, "btusb.c"), encoding="utf-8", errors="surrogateescape") as f:
            text = f.read()
        with open(os.path.join(tmp, "btusb.c"), "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(patch_recv_intr(patch_source(text, ids)))
        with open(os.path.join(tmp, "Kbuild"), "w") as f:
            f.write("obj-m := btusb.o\n")
        r = run(["make", "-C", f"{LIB_MODULES}/{rel}/build", f"M={tmp}", "modules"], timeout=BUILD_TIMEOUT)
        ko = os.path.join(tmp, "btusb.ko")
        if r.returncode != 0 or not os.path.isfile(ko):
            raise RuntimeError("Der Bau des Treibers ist fehlgeschlagen: " + (r.stdout + r.stderr)[-200:].replace("\n", " "))
        v = run(["modinfo", "-F", "vermagic", ko]).stdout.strip()
        if not v.startswith(rel):
            raise RuntimeError(f"Das gebaute Modul passt nicht zum Kernel (Modulversion '{v[:40]}')")
        return ko
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)                               # nichts liegen lassen
        raise


def adapter_present(timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if any(n.startswith("hci") for n in os.listdir("/sys/class/bluetooth")):
                return True
        except OSError:
            pass
        time.sleep(1)
    return False


def dmesg_lines():
    return run(["dmesg"], timeout=20).stdout.splitlines()


def loaded_srcversion():
    try:
        with open("/sys/module/btusb/srcversion") as f:
            return f.read().strip()
    except OSError:
        return ""


def hci_for_usb(ident, bt_root="/sys/class/bluetooth", resolve=os.path.realpath):
    """Name des Adapters (hciN), der zum USB-Gerät mit dieser Kennung gehört (über den Pfad in /sys), sonst ''."""
    try:
        names = sorted(n for n in os.listdir(bt_root) if re.fullmatch(r"hci\d+", n))
    except OSError:
        return ""
    for n in names:
        p = resolve(os.path.join(bt_root, n))
        while p and p != os.path.dirname(p):
            try:
                with open(os.path.join(p, "idVendor")) as f:
                    v = f.read().strip().lower()
                with open(os.path.join(p, "idProduct")) as f:
                    pr = f.read().strip().lower()
            except OSError:
                p = os.path.dirname(p)
                continue
            if f"{v}:{pr}" == ident:
                return n
            break
    return ""


def barrot_errors(ids, timeout=20, hci=hci_for_usb, sleep=time.sleep, lines=None):
    """Läuft jeder Barrot-Stick nach dem Laden des Treibers? Wartet, bis sein Adapter da ist, und sucht in den Kernelmeldungen nach Zeitüberschreitungen
    beim Start (genau das ist der Fehler ohne die Prüfung). lines: liefert die neuen Kernelmeldungen. Gibt Fehlertexte zurück, leer = in Ordnung."""
    bad = []
    for i in ids:
        end, name = time.time() + timeout, ""
        while True:
            name = hci(i)
            if name or time.time() >= end:
                break
            sleep(1)
        if not name:
            bad.append(f"{i}: es gibt keinen Adapter")
            continue
        sleep(6)                                                        # der Start braucht ein paar Sekunden (je Befehl zwei Sekunden Zeitüberschreitung)
        hit = [x.strip()[:160] for x in lines() if re.search(rf"{name}: .*(tx timeout|Opcode .* failed|hardware error)", x)]
        if hit:
            bad.append(f"{i}: {hit[-1]}")
    return bad


def firmware_errors(text):
    """Fehlermeldungen des Realtek-Teils im Kernelprotokoll (Firmware nicht gefunden, nicht geladen)."""
    bad = []
    for line in text.splitlines():
        if re.search(r"RTL|rtl_bt|rtl87", line) and re.search(r"fail|error|not found|-2\b|-22\b", line, re.I):
            bad.append(line.strip()[:160])
    return bad


def active_units(units):
    return [u for u in units if run(["systemctl", "is-active", "--quiet", u]).returncode == 0]


def reload_bluetooth():
    """Treiber neu laden: Dienste anhalten, btusb entladen und laden, Dienste wieder starten. Gibt zurück, ob ein Adapter da ist."""
    units = active_units(("pipbox-dji.service", "bluetooth.service"))
    for u in units:
        run(["systemctl", "stop", u], timeout=60)
    unloaded = run(["modprobe", "-r", "btusb"], timeout=60).returncode == 0
    r = run(["modprobe", "btusb"], timeout=60)
    ok = unloaded and r.returncode == 0 and adapter_present()
    for u in ("bluetooth.service", "pipbox-dji.service"):                  # Reihenfolge: erst bluetooth
        if u in units:
            run(["systemctl", "start", u], timeout=60)
    return ok


def install_module(ko, rel=None):
    rel = rel or release()
    dest = module_path(rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    backup = None
    if os.path.exists(dest):
        backup = dest + ".vorher"
        shutil.copy2(dest, backup)
    tmp = dest + ".neu"
    shutil.copy(ko, tmp)
    os.chmod(tmp, 0o644)
    os.replace(tmp, dest)
    run(["depmod", "-a", rel], timeout=120)
    return backup


def rollback(backup, rel=None):
    rel = rel or release()
    dest = module_path(rel)
    try:
        if backup and os.path.exists(backup):
            os.replace(backup, dest)
        elif os.path.exists(dest):
            os.remove(dest)
    except OSError as e:
        log(f"Rückbau: {e}")
    run(["depmod", "-a", rel], timeout=120)
    reload_bluetooth()


def do_auto():
    rel = release()
    need = wanted()
    if not need:
        return 0
    names = ", ".join(need.values())
    if not kernel_supported(rel):
        status(state="unsupported", ids=sorted(need), message=f"Für den Kernel {rel} gibt es keinen vorbereiteten Treiber ({names}). Dieser Stick läuft dort möglicherweise nicht richtig.")
        return 0
    if installed_ok(rel, need):
        status(state="ok", ids=sorted(need), message=f"Treiber für {names} ist eingerichtet.")
        return 0
    sig = signature(rel, need)
    if read_json(marker("failed.json")).get("signature") == sig:
        return 0                                                          # hat bei dieser Kombination nicht geklappt: nicht in einer Schleife versuchen
    if belacoder_running():
        status(state="waiting", ids=sorted(need), message=f"Der Treiber für {names} wird nach der Übertragung eingerichtet (er lädt Bluetooth neu).")
        return 0
    miss = tools_ok(rel)
    if miss:
        write_json(marker("failed.json"), {"signature": sig, "message": "fehlt: " + ", ".join(miss)})
        status(state="failed", ids=sorted(need), message="Der Treiber kann nicht gebaut werden, es fehlt: " + ", ".join(miss) + ".")
        return 0
    status(state="working", ids=sorted(need), message=f"Treiber für {names} wird gebaut und eingerichtet (einige Minuten) …")
    backup, tmpdir, done = None, None, False
    try:
        ko = build_module(rel, list(REALTEK_IDS))
        tmpdir = os.path.dirname(ko)
        want_src = run(["modinfo", "-F", "srcversion", ko]).stdout.strip()
        before = len(dmesg_lines())
        backup = install_module(ko, rel)
        if not reload_bluetooth():
            raise RuntimeError("Nach dem Laden des Treibers gibt es keinen Bluetooth-Adapter (oder das alte Modul ließ sich nicht entladen)")
        if want_src and loaded_srcversion() != want_src:
            raise RuntimeError("Das neue Modul wurde nicht geladen (das alte läuft noch)")
        new = dmesg_lines()[before:]
        if any(i not in BARROT for i in need):
            bad = firmware_errors("\n".join(new))
            if bad:
                raise RuntimeError("Firmware des Sticks ließ sich nicht laden: " + bad[-1])
            if not any("RTL" in x for x in new):
                raise RuntimeError("Der Treiber hat keine Firmware für den Stick geladen (keine Meldung des Realtek-Teils)")
        barrot = [i for i in need if i in BARROT]
        if barrot:
            bad = barrot_errors(barrot, lines=lambda: dmesg_lines()[before:])
            if bad:
                raise RuntimeError("Der Barrot-Stick startet auch mit dem neuen Treiber nicht: " + bad[-1])
        write_json(marker("installed.json"), {"kernel": rel, "ids": sorted(ALL_IDS), "time": int(time.time())})
        if os.path.exists(marker("failed.json")):
            os.remove(marker("failed.json"))
        done = True
        log(f"Treiber eingerichtet für {names}")
        status(state="ok", ids=sorted(need), message=f"Treiber für {names} ist eingerichtet. Eine Kamera fragt nach dem Wechsel des Sticks eventuell einmal nach der Kopplung.")
    except Exception as e:
        log(f"Treiber nicht eingerichtet: {e!r}")
        if not done:
            if os.path.exists(module_path(rel)) or backup:
                try:
                    rollback(backup, rel)
                except Exception as e2:
                    log(f"Rückbau fehlgeschlagen: {e2!r}")
            write_json(marker("failed.json"), {"signature": sig, "message": str(e)[:200]})
            status(state="failed", ids=sorted(need), message=f"Der Treiber für {names} ließ sich nicht einrichten, es läuft der Standardtreiber weiter: {str(e)[:160]}")
    finally:
        if tmpdir and tmpdir.startswith("/var/tmp/pipbox-btdriver-"):
            shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


def do_uninstall():
    rel = release()
    for p in (module_path(rel), module_path(rel) + ".vorher"):
        try:
            os.remove(p)
        except OSError:
            pass
    run(["depmod", "-a", rel], timeout=120)
    for m in ("installed.json", "failed.json"):
        try:
            os.remove(marker(m))
        except OSError:
            pass
    log("Treiber entfernt; das Standardmodul gilt nach dem nächsten Neustart wieder")
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
        return do_auto() if mode == "auto" else do_uninstall()
    except Exception as e:                                                # nie ohne Meldung enden
        log(f"Fehler: {e!r}")
        status(state="failed", message=str(e)[:200])
        return 0


if __name__ == "__main__":
    sys.exit(main())
