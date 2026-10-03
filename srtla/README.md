# Latenzbewusster SRTLA-Sender

`srtla_send-latency-aware.patch` ändert `srtla_send.c` aus [BELABOX/srtla](https://github.com/BELABOX/srtla)
(Commit `37862da3d0c13b46956efd3f88877053293d97d6`, Lizenz **AGPL-3.0**). Der Patch steht unter derselben Lizenz;
der Quelltext des gebauten Programms ist: Upstream-Commit plus dieser Patch.

Der Original-Sender verteilt Pakete nach Fenster und Paketen unterwegs, ohne die Laufzeit der Leitungen zu kennen. Eine
langsame oder wackelige Leitung staut dann die Bestätigungen, der Encoder senkt die Bitrate und erholt sich nicht mehr.
Der Patch misst je Leitung Laufzeit (RTT) und Jitter, nutzt nur die guten Leitungen (mit Hysterese) und hält die übrigen als
Reserve. Alle fünf Sekunden schreibt er eine Zeile `links: ...` je Leitung auf stderr.

Umgebungsvariablen: `SRTLA_LAT_PREF=0` schaltet die Wegewahl ab (Verhalten wie das Original), `SRTLA_LAT_MARGIN_MS=<ms>` legt
fest, wie viel schlechter als die beste Leitung eine Leitung sein darf, um noch mitzusenden (Standard: größer von 20 ms und
halber Laufzeit der besten). Die Einstellung "Verteilung auf die Sendewege = alle" der Oberfläche setzt 150 ms.

Bauen: `sudo sh build.sh` (braucht git, gcc, make, patch und Internet; installiert nach `/usr/local/bin/srtla_send`).
