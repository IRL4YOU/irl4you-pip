# SRTLA-Sender mit Wegewahl nach Laufzeit

Patches für `srtla_send.c` aus [BELABOX/srtla](https://github.com/BELABOX/srtla) (Commit `37862da3d0c13b46956efd3f88877053293d97d6`, Lizenz **AGPL-3.0**; die Patches stehen unter derselben Lizenz). Der Quelltext des gebauten Programms ist: Upstream-Commit plus diese Patches. `build.sh` wendet sie an.

- `srtla_send-latency-aware.patch`: Wegewahl nach Laufzeit (RTT) und Jitter. `SRTLA_LAT_PREF=0` schaltet sie ab, `SRTLA_LAT_MARGIN_MS=<ms>` legt den erlaubten Abstand zur besten Leitung fest.
- `srtla_send-min-share.patch`: Mindestanteil für geeignete Wege bei der Verteilung „alle“. `SRTLA_MIN_SHARE_PCT=<0-40>` (0 = aus).

Bauen: `sudo sh build.sh` (braucht git, gcc, make, patch und Internet; installiert nach `/usr/local/bin/srtla_send`).
