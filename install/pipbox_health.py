#!/usr/bin/env python3
"""Schreibt alle 10 s eine Zeile mit dem Zustand der Box in /var/log/pipbox-health.log.

Zweck: Hängt sich die Box ohne Fehlermeldung auf, zeigen die letzten Zeilen, was kurz davor los war
(Last, Temperatur, Takt, Speicher, Router, Sendekette). Auf den Datenträger erzwungen (fsync) wird nur alle 30 s,
das schont die Speicherkarte; bei einem plötzlichen Stromausfall fehlen deshalb bis zu 30 s. Die Datei bleibt unter
ca. 4 MB, etwa eine Woche (alte Zeilen fallen weg). Nur Lesen von /proc und /sys.
"""
import glob
import os
import time

LOG_DISK = "/var/log/pipbox-health.log"      # ausführlich: auf der Speicherkarte
LOG_RAM = "/run/pipbox-health.log"           # sparsam: nur im Arbeitsspeicher (nach einem Neustart weg)
MODE_FILE = "/etc/pipbox/logmode"
MAX_DISK = 4 * 1024 * 1024
MAX_RAM = 1024 * 1024
INTERVAL = 10          # Sekunden zwischen zwei Zeilen
SYNC_EVERY = 30        # Sekunden zwischen zwei Zwangs-Speicherungen (fsync)


def mode():
    """Protokoll-Modus aus /etc/pipbox/logmode; ohne Datei (ältere Installation) wie bisher auf der Karte."""
    return "sparsam" if rd(MODE_FILE) == "sparsam" else "ausfuehrlich"


def rd(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def running_names():
    """Namen aller laufenden Programme, mit einem einzigen Durchlauf durch /proc."""
    return {rd(d + "/comm") for d in glob.glob("/proc/[0-9]*")}


def line():
    names = running_names()
    load = rd("/proc/loadavg").split()[:3]
    temps = [int(t) // 1000 for t in (rd(p, "0") for p in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp"))) if t.lstrip("-").isdigit()]
    freq = [int(rd(p, "0")) // 1000 for p in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"))]
    mem = {k: int(v.split()[0]) for k, v in (l.split(":", 1) for l in rd("/proc/meminfo").splitlines())}
    return (f"{time.strftime('%H:%M:%S')} load={'/'.join(load)} temp={max(temps) if temps else '?'}C "
            f"mhz={'/'.join(map(str, freq[:8]))} memfree={mem.get('MemAvailable', 0) // 1024}M "
            f"eth1={'ja' if os.path.exists('/sys/class/net/eth1') else 'nein'} "
            f"belacoder={'ja' if 'belacoder' in names else 'nein'} srtla={'ja' if 'srtla_send' in names else 'nein'} "
            f"bt={len(glob.glob('/sys/class/bluetooth/hci[0-9]'))}")


def main():
    last_sync = 0.0
    while True:
        try:
            ram = mode() == "sparsam"
            LOG, MAX = (LOG_RAM, MAX_RAM) if ram else (LOG_DISK, MAX_DISK)
            if os.path.exists(LOG) and os.path.getsize(LOG) > MAX:
                with open(LOG, "rb") as f:
                    f.seek(-MAX // 2, 2)
                    f.readline()
                    keep = f.read()
                with open(LOG, "wb") as f:
                    f.write(keep)
            fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o644)
            try:
                os.write(fd, (line() + "\n").encode())
                if not ram and time.monotonic() - last_sync >= SYNC_EVERY:
                    os.fsync(fd)
                    last_sync = time.monotonic()
            finally:
                os.close(fd)
        except OSError:
            pass
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
