# belacoder mit toleranterem Bitraten-Regler

`belacoder-jitter-tolerant.patch` ändert die Funktion `update_bitrate()` in `belacoder.c` aus
[BELABOX/belacoder](https://github.com/BELABOX/belacoder) (Commit `ccce9ca33c8e425b33353500b95795101e847964`, Lizenz **GPL-3.0**). Der
Patch steht unter derselben Lizenz; der Quelltext des gebauten Programms ist: Upstream-Commit plus dieser Patch.

## Warum

Bei gebündelten Mobilfunkleitungen schwankt die RTT normal um 15 bis 30 ms. Der Original-Regler senkt die Bitrate schon, wenn die RTT
nur etwa 15 Prozent über ihrem Mittel liegt, und zwar immer um mindestens 100 kbit/s. Erhöhen darf er nur, wenn die RTT fast genau
auf ihrem Tiefstwert liegt, und dann nur um 30 kbit/s plus 3 Prozent pro halbe Sekunde. Nach einer kurzen Überlast (RTT-Anstieg) fällt er
deshalb auf den kleinsten Wert und bleibt dort hängen, obwohl die Leitung ein Mehrfaches hergäbe (gemessen: 1,5 statt über 12 Mbit/s,
minutenlang). Ein Neustart des Encoders behebt es nur, bis zur nächsten Überlast.

## Was der Patch ändert

- Die RTT-Schwelle zum Senken hat einen festen Mindestabstand von 30 ms über dem Mittel (`RTT_DECR_TOL_MS`).
- Die Schwelle zum Erhöhen lässt 8 ms über dem Tiefstwert zu (`RTT_INCR_TOL_MS`), und der mittlere RTT-Anstieg darf bis 0,5 ms je Messung
  betragen (vorher 0,01).
- Der leichte Senkungsschritt ist höchstens 10 Prozent der aktuellen Bitrate (mindestens 30 kbit/s) statt immer 100 kbit/s.
- Unverändert: die Schnellbremse bei echtem Stau (RTT über einem Fünftel der SRT-Latenz, großer Sendepuffer) und alle Schwellen für den Sendepuffer.

## Bauen und Benutzen

`sudo sh build.sh` (installiert nach `/opt/pipbox/bin/belacoder`; `BELACODER_DEBUG=1` baut mit Ausgabe der Reglerwerte). Die Sendekette
nimmt dieses Programm, wenn es existiert, sonst das Original aus `/usr/bin`. Rückweg: `/opt/pipbox/bin/belacoder` löschen.
