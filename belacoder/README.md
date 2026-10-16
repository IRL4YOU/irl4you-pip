# belacoder-Patches

Patches für `belacoder.c` aus [BELABOX/belacoder](https://github.com/BELABOX/belacoder) (Commit `ccce9ca33c8e425b33353500b95795101e847964`, Lizenz **GPL-3.0**; die Patches stehen unter derselben Lizenz, nicht MIT wie der Rest). Der Quelltext des gebauten Programms ist: Upstream-Commit plus diese Patches. Der Upstream-Quelltext liegt nicht im Paket, `build.sh` holt ihn von GitHub und wendet die Patches an.

Bauen: `sudo sh build.sh` (installiert nach `/opt/pipbox/bin/belacoder`). Rückweg: diese Datei löschen, dann gilt das Original aus `/usr/bin`.

**Wichtig:** Die Zahl hinter `-sb` in der Kennung (zum Beispiel `-jt2-sb12-eos`) steuert die Sendekette und wird nie hochgezählt. Neue Patches hängen nur einen Zusatz an.

Siebter Patch `belacoder-feed-shift.patch`: Neuer Steuerbefehl `shift <Zweig> <ms>` verschiebt Bild und Ton einer Kamera um die eingestellte Verzögerung. Kennung: `-sb12-eos-sh`.
