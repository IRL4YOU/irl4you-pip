# belacoder-Patches

Patches für `belacoder.c` aus [BELABOX/belacoder](https://github.com/BELABOX/belacoder) (Commit `ccce9ca33c8e425b33353500b95795101e847964`, Lizenz **GPL-3.0**; die Patches stehen unter derselben Lizenz, nicht MIT wie der Rest). Der Quelltext des gebauten Programms ist: Upstream-Commit plus diese Patches. Der Upstream-Quelltext liegt nicht im Paket, `build.sh` holt ihn von GitHub und wendet die Patches an.

Bauen: `sudo sh build.sh` (installiert nach `/opt/pipbox/bin/belacoder`). Rückweg: `/opt/pipbox/bin/belacoder` löschen, dann gilt das Original aus `/usr/bin`.

- `belacoder-jitter-tolerant.patch`: Bitraten-Regler
- `belacoder-stall-output.patch`: Stall-Wächter
- `belacoder-stats.patch`: Kennzahlen-Datei
- `belacoder-live-feeds.patch`: Kamera-Zweige im laufenden Betrieb (Bittersweet1987)
- `belacoder-frame-copy.patch`: Bildkopie
- `belacoder-no-reconfigure.patch`: Größenwechsel ohne Aussetzer
- `belacoder-audio-eos.patch`: Ton nach Kameraausfall
- `belacoder-feed-shift.patch`: Verzögerung der Kameras
- `belacoder-fast-decrease.patch`: Schnellere Regelung bei Stau
