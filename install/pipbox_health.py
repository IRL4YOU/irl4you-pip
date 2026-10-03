#!/usr/bin/env python3
"""Schreibt alle 2 s eine Zeile mit dem Zustand der Box in /var/log/pipbox-health.log.

Zweck: Hängt sich die Box ohne Fehlermeldung auf, zeigen die letzten Zeilen, was kurz davor los war
(Last, Temperatur, Takt, Speicher, Router, Sendekette). Jede Zeile wird sofort auf den Datenträger
geschrieben. Die Datei bleibt unter ca. 4 MB, etwa 1,5 Tage (alte Zeilen fallen weg). Nur Lesen von /proc und /sys.
"""
import glob
import os
import time

LOG = "/var/log/pipbox-health.log"
MAX = 4 * 1024 * 1024


def rd(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def running(name):
    for d in glob.glob("/proc/[0-9]*"):
        if rd(d + "/comm") == name:
            return True
    return False


def line():
    load = rd("/proc/loadavg").split()[:3]
    temps = [int(t) // 1000 for t in (rd(p, "0") for p in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp"))) if t.lstrip("-").isdigit()]
    freq = [int(rd(p, "0")) // 1000 for p in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"))]
    mem = {k: int(v.split()[0]) for k, v in (l.split(":", 1) for l in rd("/proc/meminfo").splitlines())}
    return (f"{time.strftime('%H:%M:%S')} load={'/'.join(load)} temp={max(temps) if temps else '?'}C "
            f"mhz={'/'.join(map(str, freq[:8]))} memfree={mem.get('MemAvailable', 0) // 1024}M "
            f"eth1={'ja' if os.path.exists('/sys/class/net/eth1') else 'nein'} "
            f"belacoder={'ja' if running('belacoder') else 'nein'} srtla={'ja' if running('srtla_send') else 'nein'} "
            f"bt={len(glob.glob('/sys/class/bluetooth/hci[0-9]'))}")


def main():
    while True:
        try:
            if os.path.exists(LOG) and os.path.getsize(LOG) > MAX:
                with open(LOG, "rb") as f:
                    f.seek(-MAX // 2, 2)
                    f.readline()
                    keep = f.read()
                with open(LOG, "wb") as f:
                    f.write(keep)
            fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                os.write(fd, (line() + "\n").encode())
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
        time.sleep(2)


if __name__ == "__main__":
    main()
