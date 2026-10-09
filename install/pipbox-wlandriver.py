#!/usr/bin/env python3
"""Root-Helfer: Treiber für USB-WLAN-Sticks mit dem Chip AIC8800D80 (zum Beispiel UGREEN AX900 WiFi 6).

Der Stick meldet sich beim Einstecken als USB-Laufwerk ("Aic MSC", a69c:5723), das nur den Windows-Treiber enthält. Der Kernel 5.10 der BELABOX hat keinen
Treiber für den Chip. Dieser Helfer holt deshalb einmal den Treiber (Quelle: github.com/shenmintao/aic8800d80, Zweig legacy-mcu1, fester Stand; GPL-2.0,
mit der Firmware des Herstellers), prüft ihn gegen eine feste SHA-256-Summe, baut daraus die Module aic_load_fw und aic8800_fdrv für den laufenden Kernel,
legt Module und Firmware ab, schaltet den Stick mit usb_modeswitch aus dem Laufwerks-Modus in den WLAN-Modus und prüft, dass eine WLAN-Schnittstelle
entsteht. Danach laden udev und der Kernel die Module beim Einstecken von selbst.

Wichtig: Dieser Stand ("legacy-mcu1") ist für Chips mit chip_mcu_id=1 (das meldet der Treiber beim Laden, auch bei deinem UGREEN AX900). Die neuere Firmware
von Radxa ist größer (358072 statt 327037 Byte) und passt dort nicht in den Speicher des Chips: der Upload bricht bei Adresse 0x170400 mit "bin upload fail" ab.

Aufruf (nur durch pipbox-wlandriver.service/.timer, keine Eingaben von außen):
  auto       prüft, ob so ein Stick steckt, und richtet ihn ein (udev beim Einstecken, Zeitgeber alle 15 min)
  uninstall  entfernt Module, Firmware und die Moduleinstellung (laufende Module werden entladen)

Sicherheitsnetz: gebaut wird in einem Temporärordner; geprüft werden Kernel, Werkzeuge und die Summe der heruntergeladenen Dateien; während einer
Übertragung passiert nichts (der Stick-Wechsel stört das Netz nicht, das Laden der Module wird aber gemieden); klappt es nicht, wird alles zurückgebaut und
für diese Kombination nicht noch einmal versucht. Kein Internet: es wird später erneut versucht.
"""
import fcntl
import hashlib
import io
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

REPO = "shenmintao/aic8800d80"
COMMIT = "1d1b8fff4627857484758d7b8a5f24ac929b933e"                          # Zweig legacy-mcu1
URL = f"https://codeload.github.com/{REPO}/tar.gz/{COMMIT}"
NEEDED = ("drivers/aic8800/aic_load_fw", "drivers/aic8800/aic8800_fdrv", "drivers/aic8800/Makefile", "drivers/aic8800/Kconfig", "fw/aic8800D80")
TREE_SHA256 = "74ddb70e55e4e1fa113e0b516a9b129cda3141a324afd4eafe1f2f73d8c2d221"          # über Pfade und Dateiinhalte der NEEDED-Teile (146 Dateien)
MAX_DOWNLOAD = 30 * 1024 * 1024
MAX_UNPACKED = 40 * 1024 * 1024
SRC_KERNEL = "5.10.160"                      # nur für diesen Kernel ist der Treiber geprüft
LIB_MODULES = "/lib/modules"
FW_DIR = "/lib/firmware/aic8800_fw/USB/aic8800D80"
MODPROBE_CONF = "/etc/modprobe.d/pipbox-aic8800.conf"
RUN = "/run/pipbox-wlandriver"
STATUS = f"{RUN}/status.json"
LOCK = "/run/pipbox-wlandriver.lock"
PERSIST = "/var/lib/pipbox-wlandriver"
SYSFS_USB = "/sys/bus/usb/devices"
SYSFS_NET = "/sys/class/net"
BUILD_TIMEOUT = 900
TMPROOT = "/var/tmp"                         # hier wird gebaut (der Dienst hat ein eigenes, privates /var/tmp)
VENDOR = "a69c"
MSC_ID = "a69c:5723"                         # Laufwerks-Modus des UGREEN AX900
LOADER_ID = "a69c:8d80"                      # Zwischenzustand: Stick im WLAN-Modus, Firmware noch nicht geladen
WORKING_ID = "a69c:8d81"                     # fertig: Firmware läuft, WLAN-Schnittstelle da
CANDIDATES = {MSC_ID: "UGREEN AX900 WLAN-Stick (AIC8800D80)", LOADER_ID: "UGREEN AX900 WLAN-Stick (AIC8800D80)"}
MODULES = ("aic_load_fw", "aic8800_fdrv")
HELPER_REV = 3                               # bei einer Änderung des Ablaufs erhöhen: ein früherer Fehlschlag gilt dann nicht mehr (neuer Versuch)
KBUILD = b"obj-m += aic_load_fw/\nobj-m += aic8800_fdrv/\n".decode("ascii")        # nur diese beiden Module bauen (das Makefile des Treibers kennt weitere Ordner)
CONF_TEXT = b"# IRL4YOU BOX: firmware of the AIC8800D80 is here\noptions aic_load_fw aic_fw_path=%s\n"          # Moduleinstellung (kein Oberflächentext)


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
    """Steckende Sticks, für die dieser Helfer zuständig ist: {Kennung: Bezeichnung}."""
    present = usb_ids_present() if present is None else present
    return {i: CANDIDATES[i] for i in sorted(CANDIDATES) if i in present}


def tree_digest(root, prefixes=NEEDED):
    """SHA-256 über Pfade und Inhalte aller Dateien unter den angegebenen Teilen (so wird der heruntergeladene Stand geprüft)."""
    rows = []
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if any(rel == x or rel.startswith(x + "/") for x in prefixes):
                with open(p, "rb") as fh:
                    rows.append((rel, hashlib.sha256(fh.read()).hexdigest()))
    rows.sort()
    return hashlib.sha256("".join("%s\0%s\n" % r for r in rows).encode()).hexdigest()


def extract_needed(data, dest):
    """Packt aus dem heruntergeladenen Archiv nur die benötigten Teile aus (nur Dateien und Ordner, keine Verknüpfungen, keine Pfade nach außen)."""
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf:
            name = m.name.split("/", 1)[1] if "/" in m.name else ""
            if not name:
                continue
            if name.startswith("/") or ".." in name.split("/"):
                raise RuntimeError("Das Archiv enthält einen ungültigen Pfad")
            if not any(name == x or name.startswith(x + "/") for x in NEEDED):
                continue
            if m.isdir():
                os.makedirs(os.path.join(dest, name), exist_ok=True)
            elif m.isreg():
                total += m.size
                if total > MAX_UNPACKED:
                    raise RuntimeError("Das Archiv ist größer als erwartet")
                target = os.path.join(dest, name)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as out:
                    out.write(tf.extractfile(m).read())
            else:
                raise RuntimeError("Das Archiv enthält eine Verknüpfung oder ein Gerät")


def download(url=None, opener=urllib.request.urlopen):
    """Lädt das Archiv des festen Stands von GitHub (höchstens MAX_DOWNLOAD Byte). Löst OSError aus, wenn es nicht geht (kein Internet)."""
    with opener(url or URL, timeout=60) as r:
        data = r.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise RuntimeError("Der Download ist größer als erwartet")
    return data


def fetch_source(dest, opener=urllib.request.urlopen):
    """Download, entpacken, Summe prüfen. Gibt den Ordner mit drivers/ und fw/ zurück."""
    extract_needed(download(opener=opener), dest)
    got = tree_digest(dest)
    if got != TREE_SHA256:
        raise RuntimeError("Die heruntergeladenen Dateien sind nicht die erwarteten (Prüfsumme)")
    return dest


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


def module_dir(rel=None):
    return f"{LIB_MODULES}/{rel or release()}/updates/aic8800"


def installed_ok(rel=None):
    """Sind unsere Module, die Firmware und die Moduleinstellung für diesen Kernel eingespielt?"""
    rel = rel or release()
    rec = read_json(marker("installed.json"))
    return (rec.get("kernel") == rel and rec.get("commit") == COMMIT
            and all(os.path.isfile(f"{module_dir(rel)}/{m}.ko") for m in MODULES)
            and os.path.isfile(f"{FW_DIR}/fmacfw_8800d80_u02.bin") and os.path.isfile(MODPROBE_CONF))


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
    miss = [t for t in ("make", "gcc", "usb_modeswitch", "depmod", "modprobe") if not shutil.which(t)]
    build = f"{LIB_MODULES}/{rel}/build"
    for f in ("Makefile", "Module.symvers", ".config", "scripts/mod/modpost"):
        if not os.path.exists(os.path.join(build, f)):
            miss.append(f"Kernel-Header ({f})")
            break
    return miss


def build_modules(src, rel=None):
    """Baut aic_load_fw.ko und aic8800_fdrv.ko in src/drivers/aic8800. Gibt {Name: Pfad} zurück."""
    rel = rel or release()
    d = os.path.join(src, "drivers", "aic8800")
    with open(os.path.join(d, "Kbuild"), "w") as f:                         # Kbuild geht vor dem Makefile des Treibers
        f.write(KBUILD)
    r = run(["make", "-j4", "-C", f"{LIB_MODULES}/{rel}/build", f"M={d}", "modules"], timeout=BUILD_TIMEOUT)
    out = {}
    for m in MODULES:
        ko = os.path.join(d, m, m + ".ko")
        if not os.path.isfile(ko):
            raise RuntimeError("Der Bau des Treibers ist fehlgeschlagen: " + (r.stdout + r.stderr)[-200:].replace("\n", " "))
        v = run(["modinfo", "-F", "vermagic", ko]).stdout.strip()
        if not v.startswith(rel):
            raise RuntimeError(f"Das gebaute Modul passt nicht zum Kernel (Modulversion '{v[:40]}')")
        out[m] = ko
    return out


def install_files(src, kos, rel=None):
    """Legt Module, Firmware und die Moduleinstellung ab. Gibt die Liste der angelegten Pfade zurück (für den Rückbau)."""
    rel = rel or release()
    made = []
    os.makedirs(module_dir(rel), exist_ok=True)
    for m, ko in kos.items():
        dest = f"{module_dir(rel)}/{m}.ko"
        shutil.copy(ko, dest + ".neu")
        os.chmod(dest + ".neu", 0o644)
        os.replace(dest + ".neu", dest)
        made.append(dest)
    os.makedirs(FW_DIR, exist_ok=True)
    fw_src = os.path.join(src, *NEEDED[-1].split("/"))
    for name in sorted(os.listdir(fw_src)):
        dest = os.path.join(FW_DIR, name)
        shutil.copy(os.path.join(fw_src, name), dest)
        os.chmod(dest, 0o644)
        made.append(dest)
    os.makedirs(os.path.dirname(MODPROBE_CONF), exist_ok=True)
    with open(MODPROBE_CONF, "w") as f:
        f.write(CONF_TEXT.decode("ascii") % FW_DIR)
    made.append(MODPROBE_CONF)
    run(["depmod", "-a", rel], timeout=120)
    return made


def remove_files(rel=None):
    rel = rel or release()
    for m in reversed(MODULES):
        run(["modprobe", "-r", m], timeout=60)
    shutil.rmtree(module_dir(rel), ignore_errors=True)
    shutil.rmtree(os.path.dirname(FW_DIR), ignore_errors=True)
    try:
        os.remove(MODPROBE_CONF)
    except OSError:
        pass
    run(["depmod", "-a", rel], timeout=120)


def netdev_for_usb(ident, net_root=SYSFS_NET, resolve=os.path.realpath):
    """Name der Netzwerkschnittstelle, die zum USB-Gerät mit dieser Kennung gehört (über den Pfad in /sys), sonst ''."""
    try:
        names = sorted(os.listdir(net_root))
    except OSError:
        return ""
    for n in names:
        p = resolve(os.path.join(net_root, n, "device"))
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


def wait_for_wlan(timeout=60, find=netdev_for_usb, sleep=time.sleep, now=time.time):
    """Wartet, bis der Stick im Arbeitsmodus (a69c:8d81) eine Schnittstelle hat. Gibt ihren Namen zurück oder ''."""
    end = now() + timeout
    while True:
        name = find(WORKING_ID)
        if name or now() >= end:
            return name
        sleep(2)


def switch_mode(present=usb_ids_present, sleep=time.sleep, now=time.time, timeout=20):
    """Stick aus dem Laufwerks-Modus holen (ändert nur den Stick). Wartet, bis er als Laufwerk verschwindet. Gibt "" bei Erfolg zurück, sonst die
    letzte Zeile von usb_modeswitch als Grund."""
    r = run(["usb_modeswitch", "-KW", "-v", VENDOR, "-p", MSC_ID.split(":")[1]], timeout=30)
    end = now() + timeout
    while MSC_ID in present():
        if now() >= end:
            last = [x.strip() for x in (r.stdout + r.stderr).splitlines() if x.strip()]
            return (last[-1] if last else "keine Antwort")[:80]
        sleep(1)
    return ""


def do_auto(opener=urllib.request.urlopen):
    rel = release()
    need = wanted()
    if not need:
        return 0
    names = ", ".join(sorted(set(need.values())))
    ids = sorted(need)
    if not kernel_supported(rel):
        status(state="unsupported", ids=ids, message=f"Für den Kernel {rel} gibt es keinen vorbereiteten Treiber ({names}). Dieser Stick läuft dort möglicherweise nicht richtig.")
        return 0
    if WORKING_ID in usb_ids_present() and netdev_for_usb(WORKING_ID):
        status(state="ok", ids=ids, message=f"Treiber für {names} ist eingerichtet.")
        return 0
    sig = hashlib.sha256(f"{rel}|{COMMIT}|{HELPER_REV}".encode()).hexdigest()[:16]
    if read_json(marker("failed.json")).get("signature") == sig:
        return 0                                                          # hat bei diesem Kernel und Stand nicht geklappt: nicht in einer Schleife versuchen
    if belacoder_running():
        status(state="waiting", ids=ids, message=f"Der Treiber für {names} wird nach der Übertragung eingerichtet (er lädt Module nach).")
        return 0
    made, tmpdir, fresh = [], None, False
    try:
        if not installed_ok(rel):
            miss = tools_ok(rel)
            if miss:
                write_json(marker("failed.json"), {"signature": sig, "message": "fehlt: " + ", ".join(miss)})
                status(state="failed", ids=ids, message="Der Treiber kann nicht gebaut werden, es fehlt: " + ", ".join(miss) + ".")
                return 0
            status(state="working", ids=ids, message=f"Treiber für {names} wird geholt, gebaut und eingerichtet (einige Minuten, braucht Internet) …")
            tmpdir = tempfile.mkdtemp(prefix="pipbox-wlandriver-", dir=TMPROOT)
            try:
                src = fetch_source(tmpdir, opener)
            except OSError as e:                                          # kein Internet, GitHub nicht erreichbar: später noch einmal, kein Merkzettel
                status(state="waiting", ids=ids, message=f"Der Treiber für {names} braucht einmal Internet zum Herunterladen (github.com), es klappt noch nicht. Die Box versucht es später erneut: {str(e)[:100]}")
                return 0
            kos = build_modules(src, rel)
            made = install_files(src, kos, rel)
            fresh = True
            write_json(marker("installed.json"), {"kernel": rel, "commit": COMMIT, "time": int(time.time())})
        if MSC_ID in usb_ids_present():
            status(state="working", ids=ids, message=f"Der Stick {names} wird in den WLAN-Modus geschaltet …")
            err = switch_mode()
            if err:
                raise RuntimeError(f"Der Stick ließ sich nicht in den WLAN-Modus schalten: {err}")
        elif LOADER_ID in usb_ids_present():
            run(["modprobe", "aic_load_fw"], timeout=60)
        name = wait_for_wlan()
        if not name:
            raise RuntimeError("Der Stick hat keine WLAN-Schnittstelle bekommen")
        if os.path.exists(marker("failed.json")):
            os.remove(marker("failed.json"))
        log(f"Treiber eingerichtet für {names}: {name}")
        status(state="ok", ids=ids, message=f"Treiber für {names} ist eingerichtet. Die WLAN-Schnittstelle heißt {name}.")
    except Exception as e:
        log(f"Treiber nicht eingerichtet: {e!r}")
        if fresh or made:
            try:
                remove_files(rel)
                if os.path.exists(marker("installed.json")):
                    os.remove(marker("installed.json"))
            except Exception as e2:
                log(f"Rückbau fehlgeschlagen: {e2!r}")
        write_json(marker("failed.json"), {"signature": sig, "message": str(e)[:200]})
        status(state="failed", ids=ids, message=f"Der Treiber für {names} ließ sich nicht einrichten: {str(e)[:160]}")
    finally:
        if tmpdir and os.path.basename(tmpdir).startswith("pipbox-wlandriver-"):
            shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


def do_uninstall():
    rel = release()
    remove_files(rel)
    for m in ("installed.json", "failed.json"):
        try:
            os.remove(marker(m))
        except OSError:
            pass
    log("Treiber entfernt")
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
