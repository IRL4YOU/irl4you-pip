# Hinweise zu Herkunft und Drittprojekten

## Moblin (MIT-Lizenz)

Die DJI-Kopplung (`dji.py`) folgt dem Ablauf, den das Projekt
[Moblin](https://github.com/eerimoq/moblin) von Erik Moqvist für DJI-Kameras
per Bluetooth umsetzt (Ordner `Moblin/Integrations/Dji/`). Nachrichtenformat,
Konstanten und Ablauf wurden von dort nach Python übertragen. Gegenprobe mit
[dimadesu/dji-remote](https://github.com/dimadesu/dji-remote) (MIT).
Das Protokoll stammt aus dem Nachbau dieser Projekte und ist von DJI nicht
dokumentiert.

### Moblin – MIT License

Copyright (c) 2023 Erik Moqvist

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## BELABOX

Unabhängiges Zusatzprojekt, kein Produkt von BELABOX, Radxa oder DJI.
Es ändert keine Dateien von BELABOX. Es nutzt die vorhandene Paketverwaltung
und liest das BELABOX-Passwort nur zur Anmeldeprüfung.

### SRTLA-Sender (AGPL-3.0)

Der Ordner `srtla/` enthält einen Patch für `srtla_send.c` aus [BELABOX/srtla](https://github.com/BELABOX/srtla)
(Commit 37862da, GNU Affero General Public License v3). Der Patch und das damit gebaute Programm stehen ebenfalls unter
AGPL-3.0; der Quelltext ist der Upstream-Commit plus dieser Patch (siehe `srtla/README.md`). Das Original-Programm des
BELABOX-Pakets bleibt unverändert unter `/usr/bin` liegen; der gepatchte Sender wird nach `/usr/local/bin` installiert.

### WLAN

Die WLAN-Karte der Oberfläche legt über `nmcli` (NetworkManager) Verbindungsprofile an, wenn Sie ein Netz verbinden.
Das Passwort wird nur an `nmcli` übergeben und von diesem Projekt nicht gespeichert.
